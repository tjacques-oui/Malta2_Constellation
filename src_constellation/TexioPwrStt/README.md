# PowerStation Constellation Satellite

## Overview

This satellite controls and monitors the **Texio PW-A powersource** (via IF-41USB) through the **Constellation** DAQ framework. It wraps the original MALTA PowerON/PowerOFF sequence and current monitoring scripts into a single Finite State Machine (FSM)-driven satellite, so that power sequencing and current telemetry are fully integrated into the Constellation ecosystem (MissionControl, Observatory, TelemetryConsole). The states are piloted by the MissionControl, logs sent to observatory and the metrics are sent to the TelemertyConsole

---

## 1. FSM State Actions

Each Constellation FSM transition maps to a specific action on the powersource.

**`initializing` → `do_initializing`**
Opens the USB connection to the IF-41USB (VID/PID from config), claims the interface, detaches the kernel driver if needed, and registers the four telemetry metrics (`IAVDD`, `IPWELL`, `IDVDD`, `ISUB`).

**`launching` → `do_launching`**
Runs the **PowerON sequence**: enables remote control (`SRMODE1`), checks/reconfigures the outputs (`_finit`), enables the main output, powers PWELL/SUB first, ramps up the voltage in steps of 100, then powers DVDD/AVDD.

**`starting` → `do_starting`**
Opens a new timestamped logfile for the run, under `./logs`, and resets the run timer.

**`running` → `do_run`**
Continuously polls the current status (`ST4`), parses `IAVdd`/`IPWell`/`IDVdd`/`ISub`, logs them to file, and pushes them as telemetry via `self.stat(...)` for real-time display in the **TelemetryConsole**.

**`stopping` → `do_stopping`**
Closes the logfile (the powersource stays on) and empties the USB response buffer.

**`landing` → `do_landing`**
Runs the **PowerOFF sequence**: enables remote control, powers down DVDD/AVDD first, ramps down the voltage in steps of 100, powers down PWELL/SUB, disables the main output, switches back to local (manual) mode, empties the buffer, then releases and disposes the USB device.

**`failure` (any state) → `fail_gracefully`**
Safety fallback: disables the main output, switches to local mode, releases the USB device, and closes the logfile if still open — regardless of which state the error occurred in.

---

## Log Files

When the satellite is `RUNNING`, output files are automatically saved to the `./logs` directory, with the run identifier and a timestamp to avoid overwriting previous sessions:

```
logs/current_log_<run_identifier>_YYYYMMDD_HHMMSS.txt
```

Each log file contains a header row followed by tab-separated values:

```
Time(s)    IAVdd(A)    IPWell(A)    IDVdd(A)    ISub(A)
```

---

## Telemetry

The following metrics are registered during `initializing` and updated on every polling cycle during `running`. They can be viewed live in the Constellation **TelemetryConsole**.

| Metric   | Unit | Description                 |
|---       |---   |---                          |
| `IAVDD`  | A    | Current on the AVDD output  |
| `IPWELL` | A    | Current on the PWELL output |
| `IDVDD`  | A    | Current on the DVDD output  |
| `ISUB`   | A    | Current on the SUB output   |

---

## Usage

1. Activate the virtual environment where `pyusb` and `ConstellationDAQ` are installed:
   ```
   source /path/to/venv/bin/activate
   ```
2. Launch the satellite (`initializing` → `initialized`):
   ```
   python3 TexioPwrStt.py -n <name> -g <group>
   ```
3. Send `launch` from MissionControl → runs the PowerON sequence.
4. Send `start` → opens the log file and begins the run.
5. During `RUNNING`, current values are logged to file and streamed to the TelemetryConsole.
6. Send `stop` → closes the log file (powersource stays on).
7. Send `land` → runs the PowerOFF sequence and releases the USB device.

---

## Requirements

- Python 3.x
- `pyusb`
- `ConstellationDAQ[cli]`

---
---

# PowerStation Constellation サテライト

## 概要

本サテライトは、**Constellation** DAQフレームワークを通じて **Texio PW-A 電源装置**(IF-41USB経由)を制御・監視します。元のMALTA電源投入/切断シーケンスおよび電流モニタリングスクリプトを、単一の有限状態機械(FSM)駆動のサテライトに統合し、電源シーケンスと電流テレメトリをConstellationのエコシステム(MissionControl、Observatory、TelemetryConsole)に完全に組み込んでいます。

---

## 1. FSM状態ごとの動作

各Constellation FSM遷移は、電源装置に対する特定の動作に対応しています。

**`initializing` → `do_initializing`**
IF-41USBへのUSB接続を開き(設定ファイルのVID/PIDを使用)、インターフェースを確保し、必要に応じてカーネルドライバをデタッチし、4つのテレメトリメトリクス(`IAVDD`、`IPWELL`、`IDVDD`、`ISUB`)を登録します。

**`launching` → `do_launching`**
**電源投入シーケンス**を実行します: リモート制御を有効化(`SRMODE1`)、出力の確認・再設定(`_finit`)、メイン出力の有効化、PWELL/SUBを先に投入、100ステップで電圧を上昇、その後DVDD/AVDDを投入します。

**`starting` → `do_starting`**
`./logs` 配下に新しいタイムスタンプ付きログファイルを開き、ランタイマーをリセットします。

**`running` → `do_run`**
電流ステータス(`ST4`)を継続的にポーリングし、`IAVdd`/`IPWell`/`IDVdd`/`ISub` を解析してファイルに記録し、`self.stat(...)` 経由でテレメトリとして送信し、**TelemetryConsole** にリアルタイム表示します。

**`stopping` → `do_stopping`**
ログファイルを閉じます(電源装置はオンのまま)。USB応答バッファを空にします。

**`landing` → `do_landing`**
**電源切断シーケンス**を実行します: リモート制御を有効化、先にDVDD/AVDDを切断、100ステップで電圧を降下、PWELL/SUBを切断、メイン出力を無効化、ローカル(マニュアル)モードに戻し、バッファを空にした後、USBデバイスを解放・破棄します。

**`failure`(任意の状態) → `fail_gracefully`**
安全フォールバック: メイン出力を無効化、ローカルモードに切替、USBデバイスを解放し、ログファイルが開いていれば閉じます — エラーが発生した状態に関わらず実行されます。

---

## ログファイルについて

サテライトが `RUNNING` 状態のとき、出力ファイルは自動的に `./logs` ディレクトリに保存されます。ファイル名にはランID とタイムスタンプが付与されるため、過去のセッションを上書きすることはありません。

```
logs/current_log_<run_identifier>_YYYYMMDD_HHMMSS.txt
```

各ログファイルには、ヘッダー行に続いてタブ区切りの数値データが記録されます。

```
Time(s)    IAVdd(A)    IPWell(A)    IDVdd(A)    ISub(A)
```

---

## テレメトリ

以下のメトリクスは `initializing` 中に登録され、`running` 中のポーリングごとに更新されます。Constellationの **TelemetryConsole** でリアルタイムに確認できます。

| メトリクス | 単位 | 説明 |
|---|---|---|
| `IAVDD` | A | AVDD出力の電流 |
| `IPWELL` | A | PWELL出力の電流 |
| `IDVDD` | A | DVDD出力の電流 |
| `ISUB` | A | SUB出力の電流 |

---

## 使用方法

1. `pyusb` と `ConstellationDAQ` がインストールされている仮想環境を有効化します:
   ```
   source /path/to/venv/bin/activate
   ```
2. サテライトを起動(`initializing` → `initialized`):
   ```
   python3 TexioPwrStt.py -n <name> -g <group>
   ```
3. MissionControlから `launch` を送信 → 電源投入シーケンスが実行されます。
4. `start` を送信 → ログファイルが開き、ランが開始されます。
5. `RUNNING` 中、電流値がファイルに記録され、TelemetryConsoleにストリーミングされます。
6. `stop` を送信 → ログファイルを閉じます(電源装置はオンのまま)。
7. `land` を送信 → 電源切断シーケンスが実行され、USBデバイスが解放されます。

---

## 動作環境

- Python 3.x
- `pyusb`
- `ConstellationDAQ[cli]`
  * Update the `parent_class` tag in the `README.md` to the satellite base class used in the code
  * Update the satellite description, parameter and metric list, custom commands of the `README.md` structure below
  * Add a configuration example for easy copy & paste
  * Add the satellite to the [Constellation Satellite Library](https://constellation.pages.desy.de/satellites/index.html) as
    described in the [Constellation Application Developer Guide](https://constellation.pages.desy.de/application_development/intro/listing.html)

---

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
