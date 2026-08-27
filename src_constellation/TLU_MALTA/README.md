---
# SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "TLU MALTA"
description: "Satellite for the Malta TLU"
category: "External"
language: "Python"
parent_class: "Satellite"
---

# MALTA TLU Constellation Satellite

## Overview

This satellite controls and monitors the **MALTA Trigger Logic Unit (TLU)** through the **Constellation** DAQ framework. Because the TLU's low-level driver (`Herakles`, the uHAL/IPbus binding) only works under Python 3.9 (as shipped by the ATLAS/LCG software stack) while Constellation requires Python ≥3.11, the satellite talks to the TLU through a **bridge subprocess** rather than importing the driver directly.

---

## Architecture: bridging two Python versions

**`Herakles.so`** (the compiled hardware driver) can only be imported under Python 3.9, and **`ConstellationDAQ`** requires Python ≥3.11 — the two cannot coexist in the same interpreter.

The solution: a small standalone script, **`tlu_bridge.py`**, launched with the Python 3.9 interpreter (from the ATLAS/LCG stack) as a **subprocess** of the satellite. It wraps `gui.tlu_service.TLUService` (the same service class used by the standalone GUI) and exposes a simple JSON-lines request/response protocol over stdin/stdout. The satellite (Python 3.11) never imports `Herakles`; it only talks to this subprocess through `TLUBridgeClient`, sending one JSON request per action (`connect`, `set_mode`, `apply_configuration`, `read_counters`, ...) and reading back one JSON response.

The bridge subprocess inherits its own `PYTHONPATH`/`LD_LIBRARY_PATH` (configured via `bridge_pythonpath`/`bridge_ld_library_path`), independently of whatever environment launched the satellite itself — this avoids depending on the exact shell/session used to start Constellation.

The **standalone GUI** (Python 3.9, PySide6, unmodified) remains a fully separate program with its own direct connection to the TLU. It is **not** integrated with the satellite or the bridge. Do not run the GUI and the satellite against the TLU at the same time — use one or the other, not both simultaneously.

---

## FSM State Actions

**`initializing` → `do_initializing`**
Launches the `tlu_bridge.py` subprocess (Python 3.9), connects to the TLU through it, reads the static configuration (mode, planes, veto/width, max rate, monitor counter), and registers the telemetry metrics.

**`launching` → `do_launching`**
Sends the mode and full configuration (planes, scintillator, veto/width, max rate) to the TLU through the bridge.

**`starting` → `do_starting`**
Resets the counters (if `reset_counters_on_start` is set), opens a timestamped logfile under `./logs`, and enables the run.

**`running` → `do_run`**
Polls the trigger counters through the bridge at `poll_interval_s`, computes the rate, and sends telemetry (`TRIGGER_COUNT`, `TRIGGER_RATE`, `TRIG_TO_MALTA`, per-plane counts) at `telemetry_interval_s`, independently from the slower text-log cadence (`status_every_s`).

**`stopping` → `do_stopping`**
Disables the run, resets counters (if `reset_counters_on_stop` is set), and closes the logfile.

**`landing` → `do_landing`**
Disconnects from the TLU and terminates the bridge subprocess.

**`failure` (any state) → `fail_gracefully`**
Safety fallback: disables the run and shuts down the bridge subprocess regardless of which state the error occurred in.

---

## ⚠️ Known issue: `reconfigure` is not currently usable

The satellite implements `do_reconfigure(self, partial_config)` to allow changing a subset of the configuration (e.g. a single plane or veto value) while in `ORBIT`, without a full `initialize`/`launch` cycle. **This is currently not usable from MissionControl**: the `reconfigure` command requires a dictionary payload (e.g. `{"plane_2": false}`), and MissionControl's basic command field only accepts a bare command name, with no way to attach a payload. Sending the command name alone fails with `TypeError: Payload must be a dictionary with configuration values`.

**Workaround (not yet validated):** use the Controller's scriptable interface instead, which accepts an explicit Python dictionary as the payload, e.g. `constellation.reconfigure("MaltaTLU.oui", {"plane_2": False})`. This has not been confirmed working end-to-end yet — treat `reconfigure` as **unsupported for now**, and use a full `initialize`/`launch` cycle to change the configuration in the meantime.

---

## Telemetry

| Metric | Unit | Description |
|---|---|---|
| `TRIGGER_COUNT` | counts | Total counts on the selected monitor counter |
| `TRIGGER_RATE` | Hz | Instantaneous trigger rate |
| `TRIG_TO_MALTA` | counts | Counts on `COUNTER_TRIG_TO_MALTA` specifically (if available) |
| `PLANE_<n>_COUNT` | counts | Counts on plane `<n>` (only for confirmed, enabled planes) |

---

## Configuration keys

**Bridge**
`bridge_python` (path to the Python 3.9 interpreter), `bridge_script` (path to `tlu_bridge.py`), `bridge_repo_root` (TLU repo root containing `gui/`), `bridge_pythonpath`, `bridge_ld_library_path` (both optional, colon-separated).

**Connection**
`uri`, `address_table` (or `adress_table`).

**Run behaviour**
`mode`, `sc_enabled`, `plane_1`..`plane_6`, `veto_1`..`veto_6`, `L1A` (veto), `width_1`..`width_6`, `width_L1A`, `max_rate_hz`, `max_rate_enabled`, `reset_counters_on_start`, `reset_counters_on_stop`, `monitor_counter` (optional override).

**Timing**
`poll_interval_s` (hardware polling), `telemetry_interval_s` (TelemetryConsole updates), `status_every_s` (text log cadence), `log_folder`.

---

## Log Files

```
logs/tlu_run_<run_identifier>_YYYYMMDD_HHMMSS.txt
```
```
Time(s)    TriggerCount
```

---

## Usage

1. Activate the virtual environment where `ConstellationDAQ` is installed (Python ≥3.11):
   ```
   source /path/to/venv/bin/activate
   ```
2. Launch the satellite:
   ```
   python3 TLU_MALTA.py -n <name> -g <group>
   ```
3. From MissionControl: `initialize` (launches the bridge, connects to the TLU) → `launch` (applies configuration) → `start` (begins the run) → `stop` → `land`.

---

## Requirements

- Python ≥3.11 (satellite) — `ConstellationDAQ[cli]`
- Python 3.9.12 (bridge, from the ATLAS/LCG stack) — `Herakles`, `gui.tlu_service`

---
---
## Configuration example

[MaltaTLU.Name]
bridge_python = "/home/atlas/sw/lcg/releases/LCG_104d/Python/3.9.12/x86_64-el9-gcc13-opt/bin/python3"
bridge_script = "/home/itdc/work/Thomas/Constellation/Bridge.py"
uri = "192.168.200.20"
address_table = "/home/MaltaSW/MaltaTLU_AlinxSW/configs/tlu_addresses.xml"
bridge_repo_root = "/home/MaltaSW/MaltaTLU_AlinxSW/"
bridge_pythonpath = "/home/MaltaSW/installed/x86_64-el9-gcc13-opt/lib:/home/MaltaSW/installed/share/lib/python"
bridge_ld_library_path = "/home/atlas/sw/lcg/releases/gcc/13.1.0-b3d18/x86_64-el9/lib64:/home/atlas/sw/lcg/releases/gcc/13.1.0-b3d18/x86_64-el9/lib:/home/MaltaSW/installed/x86_64-el9-gcc13-opt/lib"
sc_enabled = false
plane_1 = true
plane_2 = false
plane_3 = false
plane_4 = false
plane_5 = false
plane_6 = false

veto_SC = 1
veto_1 = 1
veto_2 = 1
veto_3 = 1
veto_4 = 1
veto_5 = 1
veto_6 = 1
L1A = 1000000

width_SC = 80
width_1 = 80
width_2 = 80
width_3 = 80
width_4 = 80
width_5 = 80
width_6 = 80
width_L1A = 100

# MALTA TLU Constellation サテライト

## 概要

本サテライトは、**Constellation** DAQフレームワークを通じて **MALTA トリガーロジックユニット(TLU)** を制御・監視します。TLUの低レベルドライバ(`Herakles`、uHAL/IPbusバインディング)はATLAS/LCGソフトウェアスタックが提供するPython 3.9でしか動作しない一方、ConstellationはPython 3.11以上を必要とするため、サテライトはドライバを直接インポートせず、**ブリッジ サブプロセス** を介してTLUと通信します。

---

## アーキテクチャ:2つのPythonバージョンの橋渡し

**`Herakles.so`**(コンパイル済みハードウェアドライバ)はPython 3.9でしかインポートできず、**`ConstellationDAQ`** はPython 3.11以上を要求します —— 両者は同じインタプリタ内で共存できません。

解決策として、小さな独立スクリプト **`tlu_bridge.py`** を、ATLAS/LCGスタックのPython 3.9インタプリタでサテライトの**サブプロセス**として起動します。このスクリプトは(スタンドアロンGUIと同じ)`gui.tlu_service.TLUService` をラップし、stdin/stdout経由のシンプルなJSON行ベースのリクエスト/レスポンスプロトコルを公開します。サテライト(Python 3.11)は`Herakles`を一切インポートせず、`TLUBridgeClient` を通じてこのサブプロセスとだけ通信し、各アクション(`connect`、`set_mode`、`apply_configuration`、`read_counters`など)ごとに1つのJSONリクエストを送信し、1つのJSONレスポンスを受け取ります。

ブリッジ サブプロセスは、サテライト自体を起動した環境とは独立に、独自の `PYTHONPATH`/`LD_LIBRARY_PATH`(`bridge_pythonpath`/`bridge_ld_library_path` で設定)を継承します —— これにより、Constellationを起動したシェル/セッションの正確な環境に依存する必要がなくなります。

**スタンドアロンGUI**(Python 3.9、PySide6、無変更)は、TLUへの独自の直接接続を持つ完全に独立したプログラムのままです。サテライトやブリッジとは**統合されていません**。GUIとサテライトを同時にTLUに対して実行しないでください —— どちらか一方のみを使用してください。

---

## 1. FSM状態ごとの動作

**`initializing` → `do_initializing`**
`tlu_bridge.py` サブプロセス(Python 3.9)を起動し、それを介してTLUに接続し、静的な設定(モード、プレーン、veto/width、最大レート、監視カウンタ)を読み込み、テレメトリメトリクスを登録します。

**`launching` → `do_launching`**
モードと完全な設定(プレーン、シンチレータ、veto/width、最大レート)をブリッジ経由でTLUに送信します。

**`starting` → `do_starting`**
必要であれば(`reset_counters_on_start`)カウンタをリセットし、`./logs` 配下にタイムスタンプ付きログファイルを開き、ランを有効化します。

**`running` → `do_run`**
`poll_interval_s` の間隔でブリッジ経由でトリガーカウンタをポーリングし、レートを計算し、`telemetry_interval_s` の間隔で(テキストログの間隔 `status_every_s` とは独立して)テレメトリ(`TRIGGER_COUNT`、`TRIGGER_RATE`、`TRIG_TO_MALTA`、プレーンごとのカウント)を送信します。

**`stopping` → `do_stopping`**
ランを無効化し、必要であれば(`reset_counters_on_stop`)カウンタをリセットし、ログファイルを閉じます。

**`landing` → `do_landing`**
TLUから切断し、ブリッジ サブプロセスを終了します。

**`failure`(任意の状態) → `fail_gracefully`**
安全フォールバック: エラーが発生した状態に関わらず、ランを無効化しブリッジ サブプロセスをシャットダウンします。

---

## ⚠️ 既知の問題:`reconfigure` は現在使用できません

本サテライトは `do_reconfigure(self, partial_config)` を実装しており、`ORBIT` 状態のまま、`initialize`/`launch` の全サイクルを経ずに設定の一部(例:単一のプレーンやveto値)を変更できるようにしています。**しかし現時点ではMissionControlから使用できません**: `reconfigure` コマンドは辞書型のペイロード(例:`{"plane_2": false}`)を必要としますが、MissionControlの基本的なコマンド入力欄はコマンド名のみを受け付け、ペイロードを添付する手段がありません。コマンド名のみを送信すると `TypeError: Payload must be a dictionary with configuration values` で失敗します。

**回避策(未検証)**:代わりにControllerのスクリプト実行インターフェースを使用し、明示的なPython辞書をペイロードとして渡します。例:`constellation.reconfigure("MaltaTLU.oui", {"plane_2": False})`。これはまだエンドツーエンドで動作確認できていません —— 当面は `reconfigure` を**未対応**として扱い、設定変更には完全な `initialize`/`launch` サイクルを使用してください。

---

## テレメトリ

| メトリクス | 単位 | 説明 |
|---|---|---|
| `TRIGGER_COUNT` | counts | 選択された監視カウンタの合計カウント |
| `TRIGGER_RATE` | Hz | 瞬間トリガーレート |
| `TRIG_TO_MALTA` | counts | `COUNTER_TRIG_TO_MALTA` 固有のカウント(利用可能な場合) |
| `PLANE_<n>_COUNT` | counts | プレーン `<n>` のカウント(確認済みかつ有効なプレーンのみ) |

---

## 設定キー

**ブリッジ**
`bridge_python`(Python 3.9インタプリタのパス)、`bridge_script`(`tlu_bridge.py` のパス)、`bridge_repo_root`(`gui/` を含むTLUリポジトリのルート)、`bridge_pythonpath`、`bridge_ld_library_path`(両方任意、コロン区切り)。

**接続**
`uri`、`address_table`(または `adress_table`)。

**ラン動作**
`mode`、`sc_enabled`、`plane_1`〜`plane_6`、`veto_1`〜`veto_6`、`L1A`(veto)、`width_1`〜`width_6`、`width_L1A`、`max_rate_hz`、`max_rate_enabled`、`reset_counters_on_start`、`reset_counters_on_stop`、`monitor_counter`(任意の上書き)。

**タイミング**
`poll_interval_s`(ハードウェアポーリング)、`telemetry_interval_s`(TelemetryConsole更新)、`status_every_s`(テキストログ間隔)、`log_folder`。

---

## ログファイルについて

```
logs/tlu_run_<run_identifier>_YYYYMMDD_HHMMSS.txt
```
```
Time(s)    TriggerCount
```

---

## 使用方法

1. `ConstellationDAQ` がインストールされている仮想環境(Python 3.11以上)を有効化します:
   ```
   source /path/to/venv/bin/activate
   ```
2. サテライトを起動します:
   ```
   python3 TLU_MALTA.py -n <name> -g <group>
   ```
3. MissionControlから: `initialize`(ブリッジを起動しTLUに接続) → `launch`(設定を適用) → `start`(ランを開始) → `stop` → `land`。

---

## 動作環境

- Python 3.11以上(サテライト側) — `ConstellationDAQ[cli]`
- Python 3.9.12(ブリッジ側、ATLAS/LCGスタック) — `Herakles`、`gui.tlu_service`
  * Add a configuration example for easy copy & paste
  * Add the satellite to the [Constellation Satellite Library](https://constellation.pages.desy.de/satellites/index.html) as
    described in the [Constellation Application Developer Guide](https://constellation.pages.desy.de/application_development/intro/listing.html)

---

## Configuration example

[MaltaTLU.Name]
bridge_python = "/home/atlas/sw/lcg/releases/LCG_104d/Python/3.9.12/x86_64-el9-gcc13-opt/bin/python3"
bridge_script = "/home/itdc/work/Thomas/Constellation/Bridge.py"
uri = "192.168.200.20"
address_table = "/home/MaltaSW/MaltaTLU_AlinxSW/configs/tlu_addresses.xml"
bridge_repo_root = "/home/MaltaSW/MaltaTLU_AlinxSW/"
bridge_pythonpath = "/home/MaltaSW/installed/x86_64-el9-gcc13-opt/lib:/home/MaltaSW/installed/share/lib/python"
bridge_ld_library_path = "/home/atlas/sw/lcg/releases/gcc/13.1.0-b3d18/x86_64-el9/lib64:/home/atlas/sw/lcg/releases/gcc/13.1.0-b3d18/x86_64-el9/lib:/home/MaltaSW/installed/x86_64-el9-gcc13-opt/lib"
sc_enabled = false
plane_1 = true
plane_2 = false
plane_3 = false
plane_4 = false
plane_5 = false
plane_6 = false

veto_SC = 1
veto_1 = 1
veto_2 = 1
veto_3 = 1
veto_4 = 1
veto_5 = 1
veto_6 = 1
L1A = 1000000

width_SC = 80
width_1 = 80
width_2 = 80
width_3 = 80
width_4 = 80
width_5 = 80
width_6 = 80
width_L1A = 100


## Description

This is a detailed description of the satellite and its functionality.
Possible dependencies are described alongside its features, potential pitfalls and other information.

## Parameters

The following parameters are read and interpreted by this satellite. Parameters without a default value are required.

| Parameter | Description | Type | Default Value |
| --------- | ----------- | ---- | ------------- |
| `example` | Description of the parameter | Boolean | `true` |

### Configuration Example

An example configuration for this satellite which could be dropped into a Constellation configuration as a starting point

```toml
[Template.One]
example = false
```

## Metrics

The following metrics are distributed by this satellite and can be subscribed to.

| Metric | Description | Value Type | Interval |
| ------ | ----------- | ---------- | -------- |
| `TIME` | Time since launch in seconds | Float | 10s |

## Custom Commands

This section describes all custom commands the satellite exposes to the command interface.

| Command | Description | Arguments | Return Value | Allowed States |
| ------- | ----------- | --------- | ------------ | -------------- |
| `test` | This command always returns `true` | - | Boolean, always `true` | `NEW`, `INIT`, `ORBIT` |
