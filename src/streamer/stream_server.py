"""
stream_server.py — HLS streaming HTTP server for LucidDreamer.AI.

A simple HTTP server that serves continuous HLS segments.
Can be run locally for testing.

  python stream_server.py --audio-dir /path/to/audio --port 8420

Then open http://localhost:8420/listen.m3u8 in any HLS player
(VLC, mpv, Safari, etc.)

The server:
  1. Builds a playlist from the audio directory
  2. Schedules tracks based on time of day
  3. Muxes tracks together with crossfades
  4. Segments into HLS chunks
  5. Serves the .m3u8 + segments over HTTP

"This is the transmitter. The antenna. The thing that makes
 the signal fly."
"""

from __future__ import annotations

import argparse
import html
import json
import os
import signal
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

# Allow running as script or as module
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).parent))
    from muxer import Muxer, MuxerConfig
    from playlist import Playlist, Track
    from scheduler import Scheduler
else:
    from .muxer import Muxer, MuxerConfig
    from .playlist import Playlist, Track
    from .scheduler import Scheduler


class StreamState:
    """Thread-safe stream state shared between the muxer thread and HTTP handler."""

    def __init__(self):
        self.lock = threading.Lock()
        self.now_playing: Track | None = None
        self.up_next: list[Track] = []
        self.recently_played: list[Track] = []
        self.segment_dir: str = ""
        self.playlist_path: str = ""
        self.is_streaming: bool = False
        self.listener_count: int = 0
        self.started_at: float = time.time()

    def set_now_playing(self, track: Track) -> None:
        with self.lock:
            if self.now_playing:
                self.recently_played.insert(0, self.now_playing)
                self.recently_played = self.recently_played[:10]
            self.now_playing = track

    def get_status(self) -> dict:
        with self.lock:
            return {
                "streaming": self.is_streaming,
                "now_playing": {
                    "title": self.now_playing.title if self.now_playing else None,
                    "path": self.now_playing.path if self.now_playing else None,
                } if self.now_playing else None,
                "up_next": [
                    {"title": t.title, "path": t.path} for t in self.up_next[:3]
                ],
                "recently_played": [
                    {"title": t.title, "path": t.path} for t in self.recently_played[:5]
                ],
                "listener_count": self.listener_count,
                "uptime_seconds": time.time() - self.started_at,
            }


class StreamHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves HLS segments and stream metadata."""

    # Set by main() before server starts
    state: StreamState = StreamState()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._serve_player_page()
        elif path in ("/status", "/now-playing", "/api/status"):
            self._serve_status()
        elif path in ("/listen.m3u8", "/stream.m3u8"):
            self._serve_m3u8()
        elif path.endswith(".ts"):
            self._serve_segment(path)
        else:
            # Try serving from segment directory
            self._serve_segment(path)

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if path.endswith(".ts"):
            filepath = self._resolve_segment_path(path)
            if filepath and os.path.exists(filepath):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(os.path.getsize(filepath)))
                self.end_headers()
            else:
                self.send_error(404)
        else:
            self.send_response(200)
            self.end_headers()

    def _serve_player_page(self):
        """Serve a minimal HTML page with an embedded audio player."""
        html_content = """<!DOCTYPE html>
<html>
<head>
  <title>LucidDreamer.AI — Live Stream</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      background: #0a0a0f;
      color: #c8c8d0;
      font-family: Georgia, serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
    }
    .player {
      text-align: center;
      max-width: 500px;
      padding: 2rem;
    }
    h1 {
      font-size: 1.5rem;
      font-weight: normal;
      letter-spacing: 0.05em;
      color: #8a8aa0;
    }
    .now-playing {
      font-style: italic;
      color: #6a6a80;
      margin: 1rem 0 2rem;
      font-size: 0.95rem;
    }
    audio {
      width: 100%;
      margin: 1rem 0;
    }
    .footer {
      margin-top: 2rem;
      font-size: 0.8rem;
      color: #4a4a60;
    }
    a { color: #5a7a9a; }
  </style>
</head>
<body>
  <div class="player">
    <h1>LucidDreamer.AI</h1>
    <div class="now-playing" id="now-playing">Loading stream…</div>
    <audio id="player" controls>
      <source src="/listen.m3u8" type="application/vnd.apple.mpegurl">
      Your browser does not support HLS audio.
    </audio>
    <div class="footer">
      The transmitter is warm. The signal flies.<br>
      <span id="status">—</span>
    </div>
  </div>
  <script>
    async function updateStatus() {
      try {
        const res = await fetch('/status');
        const data = await res.json();
        if (data.now_playing) {
          document.getElementById('now-playing').textContent = "♪ " + data.now_playing.title;
        }
        document.getElementById('status').textContent =
          data.streaming ? "● LIVE" : "○ OFFLINE" +
          " | " + data.listener_count + " listener(s)";
      } catch(e) {}
    }
    updateStatus();
    setInterval(updateStatus, 5000);
  </script>
</body>
</html>"""
        body = html_content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_status(self):
        """Serve current stream status as JSON."""
        status = self.state.get_status()
        body = json.dumps(status, indent=2, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_m3u8(self):
        """Serve the HLS playlist."""
        playlist_path = self.state.playlist_path
        if not playlist_path or not os.path.exists(playlist_path):
            self.send_error(503, "Stream not yet ready")
            return

        with open(playlist_path, "rb") as f:
            body = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_segment_path(self, path: str) -> str | None:
        """Resolve a segment filename to a full path."""
        filename = os.path.basename(path)
        if self.state.segment_dir:
            return os.path.join(self.state.segment_dir, filename)
        return None

    def _serve_segment(self, path: str):
        """Serve an HLS .ts segment."""
        filepath = self._resolve_segment_path(path)
        if not filepath or not os.path.exists(filepath):
            self.send_error(404, f"Segment not found: {os.path.basename(path)}")
            return

        with open(filepath, "rb") as f:
            body = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        # Only send body for GET (HEAD already sent headers)
        if self.command == "GET":
            self.wfile.write(body)

    def log_message(self, format, *args):
        """Quiet logging — only errors."""
        if args and "404" in str(args[0]):
            super().log_message(format, *args)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for handling multiple listeners."""
    daemon_threads = True


def run_stream_engine(
    state: StreamState,
    audio_dir: str,
    output_dir: str,
    config: dict | None = None,
):
    """
    Background thread: continuously muxes tracks and generates HLS segments.

    This is the heart of the transmitter. It:
      1. Loads the playlist from audio_dir
      2. Uses the scheduler to pick tracks
      3. Muxes with crossfades + normalization
      4. Segments into HLS
      5. Updates state for the HTTP server
    """
    import yaml

    # Load config
    config = config or {}
    crossfade_dur = config.get("crossfade", {}).get("duration_seconds", 3.0)
    silence_dur = config.get("inter_segment_silence", 1.0)
    norm_enabled = config.get("normalization", {}).get("enabled", True)
    target_lufs = config.get("normalization", {}).get("target_lufs", -23.0)
    hls_seg_dur = config.get("hls", {}).get("segment_duration_seconds", 10)
    hls_entries = config.get("hls", {}).get("playlist_entries", 15)

    # Build components
    playlist = Playlist(audio_dir=audio_dir)
    scheduler = Scheduler(playlist=playlist)
    muxer_config = MuxerConfig(
        crossfade_duration_seconds=crossfade_dur,
        inter_segment_silence_seconds=silence_dur,
        normalization_enabled=norm_enabled,
        target_lufs=target_lufs,
        hls_segment_duration_seconds=hls_seg_dur,
        hls_playlist_entries=hls_entries,
    )
    muxer = Muxer(muxer_config)

    # Output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    state.segment_dir = str(output_dir)
    state.playlist_path = str(Path(output_dir) / "stream.m3u8")

    print(f"[streamer] Playlist: {playlist}")
    print(f"[streamer] Scheduler: {scheduler}")
    print(f"[streamer] Muxer: {muxer}")
    print(f"[streamer] Output: {output_dir}")
    print("[streamer] Starting stream engine…")

    state.is_streaming = True

    while state.is_streaming:
        try:
            # Build a small queue (2-3 tracks)
            queue = scheduler._build_queue_with_anchors(
                size=3,
                slot=scheduler.get_current_slot(),
                now=time.time(),
                rng=__import__("random").Random(),
            )

            if not queue:
                print("[streamer] No tracks available, waiting…")
                time.sleep(10)
                continue

            print(f"[streamer] Queue: {[t.title for t in queue]}")

            # Mux the tracks
            combined = muxer.concatenate(queue)

            # Segment into HLS
            m3u8_path, segments = muxer.segment_to_hls(
                combined,
                output_dir,
            )

            state.playlist_path = m3u8_path

            # Update now playing
            for track in queue:
                state.set_now_playing(track)
                scheduler.mark_played(track)

                # Wait for the track's duration (rough estimate)
                duration = muxer.get_track_duration(track)
                if duration > 0:
                    time.sleep(min(duration, 30))  # cap at 30s for prototype
                else:
                    time.sleep(5)

            # Clean up old segments (keep last N)
            seg_files = sorted(Path(output_dir).glob("segment_*.ts"))
            max_keep = hls_entries * 2
            if len(seg_files) > max_keep:
                for old_seg in seg_files[:-max_keep]:
                    old_seg.unlink(missing_ok=True)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[streamer] Error: {e}")
            time.sleep(5)

    state.is_streaming = False
    print("[streamer] Stream engine stopped.")


def main():
    """Run the LucidDreamer streaming server."""
    parser = argparse.ArgumentParser(
        description="LucidDreamer.AI streaming audio server",
    )
    parser.add_argument(
        "--audio-dir", "-a",
        default="/home/eileen/projects/ai-writings/radio-theater/channel-42-dawn",
        help="Directory of audio files",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="./output",
        help="Directory for HLS segments",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8420,
        help="HTTP server port",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="HTTP server bind address",
    )
    args = parser.parse_args()

    # Load config
    config = {}
    if args.config and os.path.exists(args.config):
        import yaml
        with open(args.config) as f:
            config = yaml.safe_load(f)

    # Resolve output dir
    output_dir = os.path.abspath(args.output_dir)

    # Initialize state
    state = StreamState()
    StreamHandler.state = state

    # Start stream engine in background
    engine_thread = threading.Thread(
        target=run_stream_engine,
        args=(state, args.audio_dir, output_dir, config),
        daemon=True,
    )
    engine_thread.start()

    # Start HTTP server
    server = ThreadingHTTPServer((args.host, args.port), StreamHandler)
    server.timeout = 1

    print(f"\n╔══════════════════════════════════════════════════╗")
    print(f"║  LucidDreamer.AI — Stream Server                 ║")
    print(f"║  Transmitting on port {args.port:<29} ║")
    print(f"║  Listen: http://localhost:{args.port}/listen.m3u8{' '*(14-len(str(args.port)))}║")
    print(f"║  Player: http://localhost:{args.port}{' '*(19-len(str(args.port)))}║")
    print(f"║  Status: http://localhost:{args.port}/status{' '*(15-len(str(args.port)))}║")
    print(f"╚══════════════════════════════════════════════════╝\n")

    def handle_shutdown(signum, frame):
        print("\n[server] Shutting down…")
        state.is_streaming = False
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        handle_shutdown(None, None)


if __name__ == "__main__":
    main()
