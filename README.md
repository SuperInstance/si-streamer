# 🎵 Streamer

*Audio streaming muxer with time-of-day scheduling*

![🎵 Streamer](docs/images/streamer.jpg)

## What It Is

The Streamer takes audio files and turns them into a continuous broadcast. Crossfades, normalization, time-of-day scheduling, HLS output. It's the radio station's transmitter.

Morning shows in the morning. Night shows at night. The right content at the right time, automatically.

## Install

```bash
pip install superinstance-streamer
```

## Features

- Playlist management with quality-weighted scoring
- Time-of-day scheduling (morning/midday/evening/night)
- Crossfade and normalization via FFmpeg
- HLS segment output for web streaming
- Standalone HTTP streaming server included

## Quick Start

```python
from superinstance import streamer

# See docs/api/streamer-api.md for full documentation
```

## Use It For

**Internet radio station that schedules different content throughout the day**

Or anything else. This module is independently useful and Apache-2.0 licensed. Grow it for your industry. Send improvements back.

---

*Part of [LucidDreamer.AI](https://github.com/SuperInstance/luciddreamer-prototype) — built by [SuperInstance](https://github.com/SuperInstance).*
