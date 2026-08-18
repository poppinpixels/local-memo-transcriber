from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from daemon import DaemonConfig, ingest_completed_transcript, process_single_file
from obsidian_ingest import ingest_transcript


class ObsidianIngestTests(unittest.TestCase):
    def test_ingest_creates_raw_transcript_with_verified_relevant_wikilinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            vault = root / "SecondBrain"
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            target = wiki / "CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"
            target.write_text("# Mødenote\n", encoding="utf-8")

            transcript = root / "chefmoede.txt"
            transcript.write_text("Vi aftalte en skriftlig afklaring.", encoding="utf-8")

            saved = ingest_transcript(
                transcript_path=transcript,
                output_dir=vault / "raw" / "transcriptions",
                original_name="2026-08-18 Møde m Søren CIU.m4a",
                source_audio=Path("/archive/2026-08-18 Møde m Søren CIU.m4a"),
                vault_root=vault,
                rules=[
                    {
                        "id": "ciu-bibeskaeftigelse",
                        "filename_patterns": ["møde m søren ciu"],
                        "tags": ["ciu", "bibeskæftigelse"],
                        "links": ["wiki/CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"],
                    }
                ],
                now=datetime(2026, 8, 19, 0, 45),
            )

            self.assertEqual(
                saved.name,
                "2026-08-19 00-45 Morten Walsted - Møde m Søren CIU - Transskription.md",
            )
            content = saved.read_text(encoding="utf-8")
            self.assertIn("recorded: 2026-08-18", content)
            self.assertIn("links_status: linked", content)
            self.assertIn("tags: [transcription, meeting, ciu, bibeskæftigelse]", content)
            self.assertIn("- [[CIU — Bibeskæftigelse (chefmøde 2026-08-18)]]", content)
            self.assertIn("## Transskription\n\nVi aftalte en skriftlig afklaring.", content)

    def test_ingest_routes_by_transcript_content_when_filename_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            vault = root / "SecondBrain"
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            target = wiki / "Kilde — Heunicke-interviewet om strakspakken (Berlingske 2026).md"
            target.write_text("# Kilde\n", encoding="utf-8")

            transcript = root / "optagelse.txt"
            transcript.write_text("Strakspakken blev drøftet på Sorø-mødet.", encoding="utf-8")

            saved = ingest_transcript(
                transcript_path=transcript,
                output_dir=vault / "raw" / "transcriptions",
                original_name="Ny optagelse.m4a",
                source_audio=Path("/archive/Ny optagelse.m4a"),
                vault_root=vault,
                rules=[
                    {
                        "id": "soroe-moedet",
                        "transcript_patterns": ["strakspakken"],
                        "tags": ["sorø-mødet"],
                        "links": ["wiki/Kilde — Heunicke-interviewet om strakspakken (Berlingske 2026).md"],
                    }
                ],
                now=datetime(2026, 8, 19, 0, 46),
            )

            content = saved.read_text(encoding="utf-8")
            self.assertIn("routing: soroe-moedet", content)
            self.assertIn("links_status: linked", content)
            self.assertIn("- [[Kilde — Heunicke-interviewet om strakspakken (Berlingske 2026)]]", content)

    def test_completed_transcription_is_automatically_ingested_using_runtime_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            vault = root / "SecondBrain"
            target = vault / "wiki" / "CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"
            target.parent.mkdir(parents=True)
            target.write_text("# Mødenote\n", encoding="utf-8")
            transcripts_dir = root / "LocalMemoTranscriber" / "transcripts"
            transcripts_dir.mkdir(parents=True)
            basename = "2026-08-18_1807_2026-08-18-mde-m-sren-ciu"
            (transcripts_dir / f"{basename}.txt").write_text("Aftalt med CIU.", encoding="utf-8")
            rules_file = root / "transcript-links.json"
            rules_file.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "id": "ciu-bibeskaeftigelse",
                                "filename_patterns": ["møde m søren ciu"],
                                "tags": ["ciu", "bibeskæftigelse"],
                                "links": ["wiki/CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = type(
                "Config",
                (),
                {
                    "obsidian_ingest_enabled": True,
                    "obsidian_vault_dir": vault,
                    "obsidian_transcripts_dir": vault / "raw" / "transcriptions",
                    "obsidian_links_file": rules_file,
                    "transcripts_dir": transcripts_dir,
                },
            )()

            saved = ingest_completed_transcript(
                config,
                basename=basename,
                original_name="2026-08-18 Møde m Søren CIU.m4a",
                done_audio=root / "done" / f"{basename}.m4a",
            )

            self.assertIsNotNone(saved)
            self.assertIn("- [[CIU — Bibeskæftigelse (chefmøde 2026-08-18)]]", saved.read_text(encoding="utf-8"))

    def test_successful_pipeline_run_ingests_the_cleaned_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = root / "runtime"
            inbox = runtime / "inbox"
            processing = runtime / "tmp" / "processing"
            transcripts = runtime / "transcripts"
            done = runtime / "done"
            for directory in (inbox, processing, transcripts, done):
                directory.mkdir(parents=True)
            audio = inbox / "fixture.m4a"
            audio.write_bytes(b"not-real-audio")

            vault = root / "SecondBrain"
            target = vault / "wiki" / "CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"
            target.parent.mkdir(parents=True)
            target.write_text("# Mødenote\n", encoding="utf-8")
            rules_file = root / "transcript-links.json"
            rules_file.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "id": "ciu-bibeskaeftigelse",
                                "filename_patterns": ["fixture"],
                                "tags": ["ciu"],
                                "links": ["wiki/CIU — Bibeskæftigelse (chefmøde 2026-08-18).md"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            transcriber = root / "fixture_transcriber.py"
            transcriber.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import shutil, sys",
                        "input_path = Path(sys.argv[sys.argv.index('--input') + 1])",
                        "runtime = input_path.parents[2]",
                        "basename = 'fixture'",
                        "(runtime / 'transcripts').mkdir(exist_ok=True)",
                        "(runtime / 'transcripts' / f'{basename}.txt').write_text('Aftalt med CIU.', encoding='utf-8')",
                        "(runtime / 'done').mkdir(exist_ok=True)",
                        "shutil.move(input_path, runtime / 'done' / f'{basename}.m4a')",
                        "print(f'Finished transcription for {basename}.m4a; outputs: {{}}')",
                    ]
                ),
                encoding="utf-8",
            )
            config = SimpleNamespace(
                processing_dir=processing,
                watch_dir=inbox,
                supported_extensions={".m4a"},
                venv_python=Path(sys.executable),
                transcribe_script=transcriber,
                config_path=root / "config.env",
                status_script=root / "missing-status.py",
                status_file=root / "status.json",
                transcripts_dir=transcripts,
                done_dir=done,
                log_file=runtime / "runtime.log",
                error_file=runtime / "error.log",
                notify_on_success=False,
                obsidian_ingest_enabled=True,
                obsidian_vault_dir=vault,
                obsidian_transcripts_dir=vault / "raw" / "transcriptions",
                obsidian_links_file=rules_file,
            )

            self.assertTrue(process_single_file(cast(DaemonConfig, config), audio))

            saved_notes = list((vault / "raw" / "transcriptions").glob("*.md"))
            self.assertEqual(len(saved_notes), 1)
            self.assertIn(
                "- [[CIU — Bibeskæftigelse (chefmøde 2026-08-18)]]",
                saved_notes[0].read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
