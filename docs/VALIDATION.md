# Audit fixes and validation

## Development status

0.0.2 remains alpha. The original implementation contained working scaffolding but
also corruption risks, no-op writes reported as success, incomplete verification,
and disconnected GUI/settings/packaging paths. The changes in this release address
those observed failures and add repeatable regression coverage.

This is not yet a certified professional media workflow. In particular, the codec
catalog is broader than the integration fixture set. Camera-origin ProRes, DNx,
XAVC, fragmented files, multi-track material and large network-storage workloads
need representative acceptance testing with the intended NLEs.

## Regression coverage

- XMP growth and shrinkage in real MP4/MOV files, preservation of existing creator
  metadata, valid container decoding by FFprobe and unchanged media fingerprints.
- Real WAV/BWF and AIFF writes; WAV ID3 readback; AIFF save failures; BEXT loudness
  preservation and version fields; escaped XML and unrelated-property retention.
- Rejection of lossy native XMP inputs, unsupported schemas and RF64 writes.
- Real ExifTool list writes, deep inspection and failure without source changes
  for a tag ExifTool cannot write.
- Real MKV edits preserving existing tags, information-field readback and unchanged
  FFprobe packet fingerprints.
- Failed verification, terminal rollback, backup failure, source-change detection,
  unique backups, competing transactions and mandatory Windows write protection.
- CLI routing, nonzero failure exit codes, CSV export, relative manifests,
  duplicate-path rejection, worker exceptions and queued/running cancellation.
- API risk acknowledgement, read-only inventory qualifiers, cross-origin rejection,
  configuration validation and persistence.
- Browser flow: inspect a WAV, preview a timing warning, cancel without changes,
  acknowledge and write with a backup, then persist settings.

CI runs Linux and Windows integration tests with required media tools installed.
It also builds and installs the wheel outside the source tree, checks packaged GUI
assets and tool-status imports, and builds/smoke-tests the Windows console executable.
The optional browser test runs on Linux. Windows desktop file-picker/WebView2
interaction still needs manual acceptance testing.

## Limits that remain explicit

- MXF is inspection-only; RF64 cannot be written. No automatic re-wrap is offered.
- Deep inspection relies on what ExifTool can extract. Main-document tags can be
  submitted to supported writers; duplicate/embedded values are inspection-only.
- Reading an unknown tag does not make it writable. Structured editing requires
  the advanced ExifTool route, and ExifTool capability varies by file and tag.
- Native XMP language-alternative edits accept one default-language value. Native
  iXML edits are simple top-level fields, not arbitrary nested document editing.
- Per-field risk warnings are conservative classifications, not an exhaustive
  compatibility model. A passing media hash does not certify every NLE's behavior.
- Packet verification excludes metadata side data so metadata can change; it
  verifies encoded packets, timing and selected codec parameters, not every
  possible interpretation of metadata.
- Source locks prevent competing application transactions. Linux file locks are
  advisory; unrelated software must cooperate. Keep backup and verification enabled.
- Performance is not benchmarked. Large files require staging/backup disk space;
  deep embedded-data extraction can be slow. There is no NVMe auto-tuning.
- Rotation editing was deliberately deferred.

## Implementation references

- [ExifTool documentation](https://exiftool.org/exiftool_pod.html): deep extraction,
  group-qualified tags and JSON import/readback.
- [FFprobe documentation](https://ffmpeg.org/ffprobe.html): packet and extradata hashes.
- [MKVToolNix mkvpropedit](https://mkvtoolnix.download/doc/mkvpropedit.html): tag replacement
  requires preserving and merging the existing tag document.
- [EBU Tech 3285](https://tech.ebu.ch/docs/tech/tech3285.pdf): BEXT version 2 loudness
  metadata and undefined-value sentinel. Native numeric loudness fields use raw
  integer hundredths of a unit; unused measurements are not invented as zero.
- [ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew):
  Windows replacement and failure semantics.
