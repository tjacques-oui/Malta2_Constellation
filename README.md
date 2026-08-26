# Malta2_Constellation
Malta2 testbench integrated in constellation's framework, this repo only contains the satellites for the DAQ, the TLU and the Powerstation 
# MALTA Test Beam — Constellation Satellites

## Overview

This repository provides three [Constellation](https://constellation.pages.desy.de) satellites developed for the MALTA pixel detector test beam setup, each integrating a piece of existing hardware or software into the Constellation distributed DAQ framework:

- **PowerStation** — controls the Texio PW-A powersource (IF-41USB) that biases the MALTA planes.
- **MaltaDAQ** — wraps the existing `MaltaMultiDAQ` C++ readout binary and an optional Corryvreckan online monitor.
- **TLU** — controls the MALTA Trigger Logic Unit, bridged across two incompatible Python versions.

---
---

## 1. PowerStation Satellite

### Overview

Controls and monitors the Texio PW-A powersource over USB (`pyusb`), providing the AVDD/PWELL/DVDD/SUB bias voltages and currents to the MALTA planes, with live telemetry and basic safety monitoring (overcurrent warnings, short-circuit detection).

### FSM State Actions

**`initializing` → `do_initializing`**
Opens the USB connection to the IF-41USB, flushes any stale data left in the buffer, then queries the device (`PW?`) to confirm that the configured address (`PwrSttAdd`, e.g. `"PW 1"`) actually corresponds to a connected unit — raising an error (not just a warning) if no match is found, so the satellite properly fails instead of silently continuing. Reads the voltage/current setpoints (`v_avdd`, `v_dvdd`, `i_avdd`, `i_dvdd`, `v_pwell`, `v_sub`, `i_pwell`, `i_sub`) and the short-circuit detection threshold from the configuration, and registers the telemetry metrics.

**`launching` → `do_launching`**
Checks the current status and reconfigures the output limits/voltages from the configuration if they don't already match. Runs the POWER ON sequence of Malta 2 sensors

**`starting` → `do_starting`**
Opens a timestamped logfile under `./logs` for this run's current readings.

**`running` → `do_run`**
Polls the powersource status, parses the four voltages and four currents, checks them against safety thresholds (see below), sends `IAVDD`/`IPWELL`/`IDVDD`/`ISUB` to the TelemetryConsole, and appends a line to the logfile.

**`stopping` → `do_stopping`**
Closes the logfile and empties the USB buffer.

**`landing` → `do_landing`**
Runs POWER-DOWN sequence (see below) and releases the USB device.

**`failure` (any state) → `fail_gracefully`**
Runs the **same power-down sequence** as `do_landing`, with a hard `SW0` cut as a last-resort fallback only if the graceful sequence itself fails (e.g. the device stopped responding).

### Safety checks (in `do_run`)

- **Overcurrent warning**: if a measured current reaches or exceeds 90% of that output's configured maximum current, a warning is logged (the run continues).
- **Short-circuit detection**: if a measured voltage drops below a configurable fraction (`short_circuit_threshold_pct`, default 50%) of that output's configured setpoint, an error is raised, stopping the run and triggering the graceful shutdown. A relative threshold is used rather than a fixed voltage, since AVDD/DVDD (~1.8V) and PWELL/SUB (~6V) have very different nominal voltages.

### Configuration keys

| Key | Meaning |
|---|---|
| `vendor_id` / `product_id` | USB VID/PID of the IF-41USB adapter |
| `PwrSttAdd` | Serial address of this powersource (e.g. `"PW 1"`) |
| `v_avdd` / `v_dvdd` / `v_pwell` / `v_sub` | Target voltages (V) |
| `i_avdd` / `i_dvdd` / `i_pwell` / `i_sub` | Max currents (A) |
| `short_circuit_threshold_pct` | Fraction of setpoint below which a voltage is considered a short circuit (default 0.5) |
| `log_folder` / `poll_interval` | Logging directory and polling interval (s) |

---
---

## 2. MaltaDAQ Satellite

### Overview

Wraps the existing `MaltaMultiDAQ` C++ binary (ROOT/TApplication-based readout of the MALTA planes) and an optional Corryvreckan-based online monitor, Does not send data in constellation but uses the Online Monitor to do it.

### FSM State Actions

**`initializing`**
Reads the configuration: `binary_path`, `daq_config`, `work_dir`, `output_dir`, `monitor_script` (default `run_onlinemonitor.sh`), `monitor_dir`.

**`starting` → `do_starting(run_id)`**
Splits the Constellation run identifier (e.g. `run_71`) on the **last** underscore to get an integer run number for the binary's `-r` argument, creates the run's output directory if needed, launches `MaltaMultiDAQ` with `cwd` set to `work_dir` (so its own relative config paths resolve correctly), and waits up to 60 seconds for a `"Start"` line on its stdout/stderr before considering the launch successful — raising an error and cleaning up otherwise.

**`RUN` → `do_run`**
On the first call for a run, launches the online monitor (`run_onlinemonitor.sh <run_number>`) as a non-blocking background process; subsequent calls do nothing further.

**`stopping` → `do_stopping`**
Sends `SIGTERM` to the DAQ binary so its signal handler can close the ROOT output files properly, waiting up to 3 minutes before falling back to a kill. The monitor process is stopped the same way with a shorter timeout.

**`landing`**
Clears the process handles.

### Environment note — ROOT/RPATH conflict

`MaltaMultiDAQ` and `corry` (Corryvreckan) depend on different, incompatible ROOT installations, and `corry`'s ROOT path is hard-coded in its RPATH (not overridable via `LD_LIBRARY_PATH`, unlike a RUNPATH). `run_onlinemonitor.sh` must therefore start from a clean environment (`unset LD_LIBRARY_PATH`/`ROOTSYS`/`PYTHONPATH`) before calling `corry` — injecting the LCG_104d ROOT/gcc paths causes `undefined symbol` errors from ABI incompatibility (ROOT 6.28.12 vs 6.32.02).

### Configuration example

```toml
binary_path = "/home/MaltaSW/build/MaltaDAQ/MaltaMultiDAQ"
daq_config =  "/home/MaltaSW/MaltaDAQ/configs/2DUT_Const_Test.txt"
work_dir = "/home/MaltaSW/MaltaDAQ"
monitor_dir = "/home/MaltaSW/ReadyJuneBeamtest/config/2malta_dut"
monitor_script = "run_onlinemonitor.sh"
_conditions.require_stopping_after = ["MaltaTLU.TLU"] 
```
The last configuration element is here to wait for the TLU to stop its run before stopping the DAQ
---
---

## 3. TLU Satellite

### Overview

Controls and monitors the MALTA Trigger Logic Unit (TLU). Because the TLU's low-level driver (`Herakles`, the uHAL/IPbus binding) only works under Python 3.9 (the ATLAS/LCG software stack), while Constellation requires Python ≥3.11, the satellite talks to the TLU through a **bridge subprocess** rather than importing the driver directly.

### Architecture: bridging two Python versions

A small standalone script, `tlu_bridge.py`, is launched with the Python 3.9 interpreter as a subprocess of the satellite. It wraps `gui.tlu_service.TLUService` (the same service class used by the standalone GUI) and exposes a JSON-lines request/response protocol over stdin/stdout. The satellite (Python 3.11) never imports `Herakles`; it only talks to this subprocess through `TLUBridgeClient`.

The **standalone GUI** (Python 3.9, PySide6, unmodified) remains a fully separate program with its own direct connection to the TLU — it is not integrated with the satellite, and should not be run against the same TLU at the same time as the satellite.

### FSM State Actions

**`initializing` → `do_initializing`**
Launches the `tlu_bridge.py` subprocess, connects to the TLU through it, reads the configuration (mode, planes, veto/width, max rate, monitor counter), and registers the telemetry metrics.

**`launching` → `do_launching`**
Sends the mode and full configuration (planes, scintillator, veto/width, max rate) to the TLU through the bridge.

**`starting` → `do_starting`**
Resets the counters (if requested) and enables the run.

**`running` → `do_run`**
Polls the trigger counters through the bridge, computes and sends telemetry as a snapshot "at time T" (new counts since the last update, not the ever-growing hardware counter value) at `telemetry_interval_s`, independently of the slower text-log cadence.

**`stopping` → `do_stopping`**
Disables the run, writes a full run summary CSV (`field,value` format: configuration used, plus count/current/average/peak rate for the main counter and each plane), and resets counters if requested.

**`landing` → `do_landing`**
Disconnects from the TLU and terminates the bridge subprocess.

**`failure` (any state) → `fail_gracefully`**
Disables the run, attempts to write a run summary (marked `failed`) if a run had started, and shuts down the bridge safely.

### Busy-line monitoring

Trigger planes 1/2/3 each have a corresponding busy-line input, wired to planes 4/5/6 respectively. Enabling trigger plane N automatically also monitors its busy line (`BUSY_N_COUNT`) in telemetry and in the run summary CSV — no separate configuration needed; the busy line is never included in the actual trigger coincidence logic sent to the hardware.

### Telemetry

| Metric | Unit | Description |
|---|---|---|
| `TRIGGER_COUNT` | counts | New triggers on the monitor counter since the last telemetry update |
| `TRIGGER_RATE` | Hz | Instantaneous trigger rate |
| `TRIG_TO_MALTA` | counts | New counts on `COUNTER_TRIG_TO_MALTA` specifically (if available) |
| `PLANE_<n>_COUNT` | counts | New counts on trigger plane `<n>` |
| `BUSY_<n>_COUNT` | counts | New counts on the busy line for plane `<n>` (auto-enabled) |

### ⚠️ Known issue: `reconfigure` is not currently usable from MissionControl

The satellite implements `do_reconfigure` to allow changing part of the configuration without a full `initialize`/`launch` cycle. This method cannot be called using the GUI controller, it can ONLY be done YET via the Controller's scriptable interface (passing an explicit Python dictionary) .

### Configuration keys

| Key | Meaning |
|---|---|
| `bridge_python` | Path to the Python 3.9 interpreter with `Herakles` available |
| `bridge_script` | Path to `tlu_bridge.py` |
| `bridge_repo_root` | Root of the TLU repo (containing `gui/`), passed to the bridge |
| `bridge_pythonpath` / `bridge_ld_library_path` | Extra paths the bridge subprocess needs to import `Herakles` and its shared libraries, independent of whatever environment launched the satellite |
| `uri` / `address_table` | TLU network address and register map XML |
| `mode`, `plane_1`..`plane_6`, `sc_enabled` | Trigger mode and active planes/scintillator |
| `veto_1`..`veto_6`, `L1A`, `width_1`..`width_6`, `width_L1A` | Veto/width windows (ns) per channel |
| `max_rate_hz` / `max_rate_enabled` | L1A max-rate limiting |
| `monitor_counter` | Optional override of the auto-selected main counter |
| `poll_interval_s` / `telemetry_interval_s` / `status_every_s` | Polling, telemetry, and log cadence |
| `_conditions.require_starting_after = ["MaltaDAQ.W4R1_W2R6"]` | requires the DAQ to be running before starting the run
---
---
---

# MALTAテストビーム — Constellationサテライト

## 概要

本リポジトリは、MALTAピクセル検出器テストビーム環境向けに開発された3つの[Constellation](https://constellation.pages.desy.de)サテライトを提供します。それぞれが既存のハードウェアまたはソフトウェアをConstellation分散DAQフレームワークに統合しています。

- **PowerStation** — MALTAプレーンにバイアスを供給するTexio PW-A電源装置(IF-41USB)を制御します。
- **MaltaDAQ** — 既存のC++読み出しバイナリ `MaltaMultiDAQ` と、任意のCorryvreckanオンラインモニターをラップします。
- **TLU** — MALTAトリガーロジックユニットを制御します。互換性のない2つのPythonバージョンを橋渡しします。

---
---

## 1. PowerStationサテライト

### 概要

Texio PW-A電源装置をUSB(`pyusb`)経由で制御・監視し、MALTAプレーンにAVDD/PWELL/DVDD/SUBのバイアス電圧・電流を供給します。リアルタイムのテレメトリと基本的な安全監視(過電流警告、短絡検出)を備えています。

### FSM状態ごとの動作

**`initializing` → `do_initializing`**
IF-41USBへのUSB接続を開き、バッファに残っている古いデータを消去した後、デバイスに問い合わせ(`PW?`)、設定されたアドレス(`PwrSttAdd`、例:`"PW 1"`)が実際に接続されているユニットに対応しているかを確認します。一致しない場合は、単なる警告ではなくエラーを発生させ、サテライトがそのまま続行せず正しく失敗するようにします。設定から電圧/電流の目標値(`v_avdd`、`v_dvdd`、`i_avdd`、`i_dvdd`、`v_pwell`、`v_sub`、`i_pwell`、`i_sub`)と短絡検出のしきい値を読み込み、テレメトリメトリクスを登録します。

**`launching` → `do_launching`**
電源投入シーケンスを実行します: リモート制御を有効化、現在のステータスを確認し設定と一致していなければ出力制限/電圧を再設定、メイン出力を有効化、PWELL/SUBの電圧を段階的に上昇、その後DVDD/AVDDを有効化します。

**`starting` → `do_starting`**
このランの電流測定値を記録するため、`./logs` 配下にタイムスタンプ付きログファイルを開きます。

**`running` → `do_run`**
電源装置のステータスをポーリングし、4つの電圧と4つの電流を解析し、安全しきい値と照合し(下記参照)、`IAVDD`/`IPWELL`/`IDVDD`/`ISUB` をTelemetryConsoleに送信し、ログファイルに1行追記します。

**`stopping` → `do_stopping`**
ログファイルを閉じ、USBバッファを空にします。

**`landing` → `do_landing`**
完全な電源切断シーケンス(下記参照)を実行し、USBデバイスを解放します。

**`failure`(任意の状態) → `fail_gracefully`**
`do_landing` と**同じ完全な電源切断シーケンス**(急な遮断ではなく降圧シーケンス)を実行します。この降圧シーケンス自体が失敗した場合(デバイスが応答しなくなった場合など)にのみ、最終手段として `SW0` による強制遮断にフォールバックします。

### 安全チェック(`do_run` 内)

- **過電流警告**: 測定電流がそのの出力に設定された最大電流の90%以上に達した場合、警告がログに記録されます(ランは継続します)。
- **短絡検出**: 測定電圧がその出力の設定電圧の設定可能な割合(`short_circuit_threshold_pct`、デフォルト50%)を下回った場合、エラーが発生しランが停止され、安全な電源切断がトリガーされます。AVDD/DVDD(約1.8V)とPWELL/SUB(約6V)では公称電圧が大きく異なるため、固定電圧ではなく相対的なしきい値を使用しています。

### 既知の問題・確認すべき点

- `_finit` 内の「既に設定済みのため再初期化不要」の高速パスは、デバイスのステータス文字列を設定値から構築した文字列と比較します。デバイスがPythonのデフォルトの `str()` と異なる小数点桁数でフォーマットする場合(例: `"0.7"` に対して `"0.70"`)、この比較が一致しない可能性があります — これは無害で、単に既に正しい場合でもスキップされず毎回再設定されるだけです。

### 設定キー

| キー | 意味 |
|---|---|
| `vendor_id` / `product_id` | IF-41USBアダプタのUSB VID/PID |
| `PwrSttAdd` | この電源装置のシリアルアドレス(例: `"PW 1"`) |
| `v_avdd` / `v_dvdd` / `v_pwell` / `v_sub` | 目標電圧(V) |
| `i_avdd` / `i_dvdd` / `i_pwell` / `i_sub` | 最大電流(A) |
| `short_circuit_threshold_pct` | この割合を下回ると短絡とみなされる、設定電圧に対する割合(デフォルト0.5) |
| `log_folder` / `poll_interval` | ログ保存先ディレクトリとポーリング間隔(秒) |

---
---

## 2. MaltaDAQサテライト

### 概要

既存のC++バイナリ `MaltaMultiDAQ`(MALTAプレーンの読み出しを行う、ROOT/TApplicationベースのプログラム)と、任意のCorryvreckanベースのオンラインモニターをラップし、これら2つの外部プロセスの**ライフサイクル管理のみ**を行います。DAQのロジック自体は再実装していません。

### FSM状態ごとの動作

**`initializing`**
設定を読み込みます: `binary_path`、`daq_config`、`work_dir`、`output_dir`、`monitor_script`(デフォルト `run_onlinemonitor.sh`)、`monitor_dir`。

**`starting` → `do_starting(run_id)`**
Constellationのラン識別子(例: `edda_71`)を**最後の**アンダースコアで分割し、バイナリの `-r` 引数用の整数のラン番号を取得します。必要であればランの出力ディレクトリを作成し、`cwd` を `work_dir` に設定して `MaltaMultiDAQ` を起動します(これにより、バイナリ自身の相対設定パスが正しく解決されます)。起動が成功したとみなす前に、標準出力/標準エラー出力に `"Start"` という行が現れるまで最大60秒待機します。現れない場合はエラーを発生させクリーンアップします。

**`RUN` → `do_run`**
あるランに対する最初の呼び出し時に、オンラインモニター(`run_onlinemonitor.sh <run_number>`)を非同期のバックグラウンドプロセスとして起動します。以降の呼び出しでは何も行いません。

**`stopping` → `do_stopping`**
DAQバイナリに `SIGTERM` を送信し(最初に `SIGKILL` を使うことは決してありません)、シグナルハンドラがROOT出力ファイルを適切にクローズできるようにし、最大20秒待機した後、必要であればkillにフォールバックします。モニタープロセスも同様の方法で、より短いタイムアウトで停止されます。

**`landing`**
プロセスハンドルをクリアします。

### 環境に関する注意 — ROOT/RPATHの競合

`MaltaMultiDAQ` と `corry`(Corryvreckan)は、互換性のない異なるROOTインストールに依存しており、`corry` のROOTパスはRPATHにハードコードされています(RUNPATHとは異なり `LD_LIBRARY_PATH` では上書きできません)。そのため `run_onlinemonitor.sh` は `corry` を呼び出す前に、必ずクリーンな環境(`unset LD_LIBRARY_PATH`/`ROOTSYS`/`PYTHONPATH`)から開始する必要があります — LCG_104dのROOT/gccパスを注入すると、ABI非互換性(ROOT 6.28.12 と 6.32.02)により `undefined symbol` エラーが発生します。

### 既知の制限事項

- `run_id` が常に `prefix_number` の形式であることを前提としています。アンダースコアを含まないIDはエラーになります。
- 起動検出は `MaltaMultiDAQ` の標準出力における正確な `"Start"` の文言に依存しています。このログ出力が変更された場合は更新が必要です。
- オンラインモニターは、ラン中にクラッシュした場合の自動再起動は行われません。

### 設定例

```toml
binary_path = "/home/MaltaSW/build/MaltaDAQ/MaltaMultiDAQ"
daq_config = "/home/MaltaSW/MaltaDAQ/configs/Malta2_W4R1_Initial.txt"
work_dir = "/home/MaltaSW/MaltaDAQ"
monitor_dir = "/home/MaltaSW/ReadyJuneBeamtest/config/2malta_dut"
```

---
---

## 3. TLUサテライト

### 概要

MALTAトリガーロジックユニット(TLU)を制御・監視します。TLUの低レベルドライバ(`Herakles`、uHAL/IPbusバインディング)はATLAS/LCGソフトウェアスタックが提供するPython 3.9でしか動作しない一方、ConstellationはPython 3.11以上を必要とするため、サテライトはドライバを直接インポートせず、**ブリッジ サブプロセス** を介してTLUと通信します。

### アーキテクチャ:2つのPythonバージョンの橋渡し

小さな独立スクリプト `tlu_bridge.py` が、Python 3.9インタプリタでサテライトのサブプロセスとして起動されます。これは(スタンドアロンGUIと同じ)`gui.tlu_service.TLUService` をラップし、stdin/stdout経由のJSON行ベースのリクエスト/レスポンスプロトコルを公開します。サテライト(Python 3.11)は `Herakles` を一切インポートせず、`TLUBridgeClient` を通じてこのサブプロセスとだけ通信します。

**スタンドアロンGUI**(Python 3.9、PySide6、無変更)は、TLUへの独自の直接接続を持つ完全に独立したプログラムのままです — サテライトとは統合されておらず、サテライトと同時に同じTLUに対して実行すべきではありません。

### FSM状態ごとの動作

**`initializing` → `do_initializing`**
`tlu_bridge.py` サブプロセスを起動し、それを介してTLUに接続し、設定(モード、プレーン、veto/width、最大レート、監視カウンタ)を読み込み、テレメトリメトリクスを登録します。

**`launching` → `do_launching`**
モードと完全な設定(プレーン、シンチレータ、veto/width、最大レート)をブリッジ経由でTLUに送信します。

**`starting` → `do_starting`**
必要であればカウンタをリセットし、ランを有効化します。

**`running` → `do_run`**
ブリッジ経由でトリガーカウンタをポーリングし、テレメトリ間隔ごとに「時刻Tにおけるスナップショット」(前回更新以降の新規カウント数であり、増加し続けるハードウェアカウンタの生値ではない)としてテレメトリを計算・送信します。この間隔はテキストログの(より長い)間隔とは独立しています。

**`stopping` → `do_stopping`**
ランを無効化し、ラン全体のサマリーCSV(`field,value` 形式:使用した設定に加え、メインカウンタと各プレーンのカウント数/現在・平均・最大レート)を書き出し、必要であればカウンタをリセットします。

**`landing` → `do_landing`**
TLUから切断し、ブリッジ サブプロセスを終了します。

**`failure`(任意の状態) → `fail_gracefully`**
ランを無効化し、ランが既に開始されていた場合はサマリー(`failed` とマーク)の書き出しを試み、ブリッジを安全にシャットダウンします。

### ビジーライン監視

トリガープレーン1/2/3にはそれぞれ対応するビジーライン入力があり、それぞれプレーン4/5/6に配線されています。トリガープレーンNを有効にすると、対応するビジーライン(`BUSY_N_COUNT`)もテレメトリとサマリーCSVに自動的に含まれます — 別途設定は不要です。ビジーラインがハードウェアに送信される実際のトリガー一致ロジックに含まれることは決してありません。

### テレメトリ

| メトリクス | 単位 | 説明 |
|---|---|---|
| `TRIGGER_COUNT` | counts | 前回のテレメトリ更新以降の、監視カウンタにおける新規トリガー数 |
| `TRIGGER_RATE` | Hz | 瞬間トリガーレート |
| `TRIG_TO_MALTA` | counts | `COUNTER_TRIG_TO_MALTA` 固有の新規カウント数(利用可能な場合) |
| `PLANE_<n>_COUNT` | counts | トリガープレーン `<n>` の新規カウント数 |
| `BUSY_<n>_COUNT` | counts | プレーン `<n>` のビジーラインの新規カウント数(自動有効化) |

### ⚠️ 既知の問題:`reconfigure` は現在MissionControlから使用できません

本サテライトは `do_reconfigure` を実装しており、完全な `initialize`/`launch` サイクルを経ずに設定の一部を変更できるようにしています。MissionControlの基本的なコマンド入力欄はコマンド名のみを受け付け、必要な辞書型のペイロードを添付する手段がありません — `reconfigure` のみを送信すると失敗します。Controllerのスクリプト実行インターフェース(明示的なPython辞書を渡す)経由の回避策は、まだエンドツーエンドで動作確認できていません。当面は `reconfigure` を未対応として扱い、設定変更には完全な `initialize`/`launch` サイクルを使用してください。

### 設定キー

| キー | 意味 |
|---|---|
| `bridge_python` | `Herakles` が利用可能なPython 3.9インタプリタのパス |
| `bridge_script` | `tlu_bridge.py` のパス |
| `bridge_repo_root` | `gui/` を含むTLUリポジトリのルート(ブリッジに渡される) |
| `bridge_pythonpath` / `bridge_ld_library_path` | ブリッジ サブプロセスが `Herakles` とその共有ライブラリをインポートするために必要な追加パス。サテライトを起動した環境とは独立 |
| `uri` / `address_table` | TLUのネットワークアドレスとレジスタマップXML |
| `mode`、`plane_1`〜`plane_6`、`sc_enabled` | トリガーモードと有効なプレーン/シンチレータ |
| `veto_1`〜`veto_6`、`L1A`、`width_1`〜`width_6`、`width_L1A` | チャンネルごとのveto/widthウィンドウ(ns) |
| `max_rate_hz` / `max_rate_enabled` | L1A最大レート制限 |
| `monitor_counter` | 自動選択されるメインカウンタの任意の上書き |
| `poll_interval_s` / `telemetry_interval_s` / `status_every_s` | ポーリング、テレメトリ、ログの間隔 |
