#!/usr/bin/env python3
"""
Monster Archiver — Nexus Edition

Audio archiver: fingerprints audio files, fetches metadata and lyrics from
MusicBrainz, iTunes, and Deezer, AI-transcribes vocals via Whisper with
Demucs stem separation, translates lyrics through a choice of cloud or local
LLM engines, and writes fully-tagged FLAC / MP3 / M4A files into an
organised library.

This file is now a thin entry point — the actual implementation lives in the
monster_archiver/ package (see monster_archiver/cli.py for the argument
parsing and session routing, and the other monster_archiver/*.py modules for
each feature area: config, db, metadata, lyrics, whisper_transcribe,
translation, tag_writer, library_scan, album_merge, watch_daemon, etc.).
Kept at this path/name so webapp/server.ts's subprocess calls and the
run_windows.bat / run_mac_linux.sh launchers don't need to change.
"""
from monster_archiver.cli import main

if __name__ == "__main__":
    main()
