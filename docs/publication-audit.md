# Publication audit

Status: **approved for initial publication** as the public repository
`stackchan-live` under the MIT license. The repeatable boundary and
high-confidence credential scan passed before the initial commit.

## Proposed public structure

| Path | Publish | Reason |
| --- | --- | --- |
| `README.md`, `pixi.toml`, `pixi.lock` | Yes | Project entry point and reproducible Apple Silicon environment |
| `server/src/`, `server/tests/`, `server/pyproject.toml` | Yes | Realtime server, memory, audio pipeline, tools, and regression coverage |
| `intelligence/agent/`, package manifests | Yes | Eve instructions, authored tools, approvals, skills, and tests |
| `firmware/src/`, nonprivate headers, `platformio.ini` | Yes | Original ESP32-S3 implementation and hardware safety boundary |
| Original face source sheet and build script | Yes | Original project artwork and deterministic asset generation |
| `docs/` | Yes | Architecture, protocol, evidence, model decision, and setup guide |
| HIL and benchmark scripts | Yes | Reproducible validation and performance work |
| `third_party/M5_Hardware/` | Yes | Official mechanical STL files with upstream revision, notice, and MIT license |

Generated face PNGs, the generated C++ byte arrays, and all build products are
recreated locally and are not part of the proposed commit.

## Explicit exclusions

| Excluded data | Why |
| --- | --- |
| `.env*` except `.env.example` | May contain tokens or machine-specific settings |
| `secrets/`, `DeviceSecret.hpp` | Device pairing credential |
| `LocalConfig.hpp` | Machine-specific Bonjour host |
| `artifacts/` | Models, audio, transcripts, benchmark traces, logs, and local evidence |
| `server/data/` | Personal long-term memory and conversation episodes |
| `.pixi/`, virtual environments, caches, `node_modules/`, Eve state | Downloaded or private runtime state |
| PlatformIO and generated firmware output | Reproducible build artifacts, including binaries |
| Factory flash backup | Can contain saved Wi-Fi; moved outside the project |
| Deferred simulator | Incomplete and previously contained a nonredistributable product photo; moved outside the project |

The ignore rules apply to all of these categories rather than relying only on
the current filenames.

## Source and license findings

- Application firmware, server, intelligence integration, face renderer, and
  face artwork are original to this project; the vendor application and vendor
  UI are not included.
- The official M5Stack mechanical STL snapshot is isolated under `third_party/`
  with the upstream revision and MIT license.
- Runtime dependencies are pinned in `pixi.lock` and
  `intelligence/package-lock.json`; their own upstream licenses continue to
  apply.
- The original project code and artwork use the root MIT license. The bundled
  M5Stack mechanical assets retain their separate upstream MIT copyright and
  notice.

## Automated checks before publication

The final pre-publish pass must verify:

1. `git check-ignore` covers each real local secret, memory, model, log, backup,
   and generated build path.
2. A credential-pattern scan reports no likely secrets in the exact candidate
   file set.
3. The largest candidate files are expected source artwork or licensed STL
   assets, not models, logs, firmware binaries, or backups.
4. `pixi run check` passes from the proposed source structure.
5. Markdown links and the setup commands resolve.
6. The user confirms repo name, public/private visibility, license, and exact
   structure before any commit, GitHub creation, push, or release. This was
   confirmed as `stackchan-live`, public, MIT on 2026-08-08.

Run the repeatable boundary and high-confidence credential scan with:

```sh
pixi run publication-audit
```

## Naming shortlist

1. **stackchan-live** (recommended): clear hardware association and realtime
   conversation focus without tying the architecture to one model provider.
2. **stackchan-local**: matches the current package names and emphasizes local
   STT/TTS, but understates the hosted intelligence option.
3. **kokoro-chan**: warmer and more brandable, but less discoverable to people
   looking specifically for Stack-chan firmware.
4. **stackchan-eve**: immediately explains the current intelligence layer, but
   couples the project name to a replaceable adapter.

The package names can remain `stackchan-local` even if the repository is named
`stackchan-live`.
