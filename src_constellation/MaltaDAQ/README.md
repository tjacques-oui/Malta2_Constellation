---
# SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "MALTA DAQ Satellite"
description: "Satellite for the piloting of MALTA DAQ and Online Monitor"
category: "External"
language: "Python"
parent_class: "MaltaDAQ"
---
# MaltaDAQ Satellite

**EN / 日本語 bilingual README**

---

## English

### Overview

`MaltaDAQ.py` is a [Constellation](https://gitlab.cern.ch/constellation) satellite that integrates the MALTA multi-plane DAQ system into the Constellation distributed DAQ framework. It wraps the existing `MaltaMultiDAQ` C++ binary (the ROOT/TApplication-based readout program for the MALTA telescope planes) and optionally launches a Corryvreckan-based online monitor during the run, so that live tracking/correlation plots can be viewed independently of the Constellation telemetry console.

The satellite does not reimplement any DAQ logic. It only manages the **lifecycle** of two external processes:

1. `MaltaMultiDAQ` — the C++ readout binary that configures the MALTA planes, opens the sockets, and writes ROOT files.
2. `run_onlinemonitor.sh` — a bash wrapper that launches `corry` (Corryvreckan) with the best available geometry, to visualize data live.

### How it works

#### State: `initializing`

Reads the satellite configuration (TOML) and stores the paths needed later:

| Config key       | Meaning                                                                                                                   |
|------------------|---------------------------------------------------------------                                                            |
| `binary_path`    | Absolute path to the compiled `MaltaMultiDAQ` executable                                                                  |
| `daq_config`     | Path to the MALTA readout config file (`-c` argument)                                                                     |
| `work_dir`       | Working directory for the DAQ binary (so relative paths inside its config, e.g. tap calibration files, resolve correctly) |
| `output_dir`     | Base directory where ROOT files are written                                                                               |
| `monitor_script` | Name of the online monitor shell script (default: `run_onlinemonitor.sh`)                                                 |
| `monitor_dir`    | Working directory for the online monitor script                                                                           |

#### State: `starting` → `do_starting(run_id)`

Constellation run identifiers are strings, often in the form `prefix_number` (e.g. `edda_71`), while the C++ binary expects a plain **integer** for its `-r` argument. This method:

1. Splits `run_id` on the **last** underscore (`str.rpartition('_')`) to separate the prefix from the run number, so prefixes containing underscores are handled correctly.
2. Converts the number part to an integer; raises an error if it isn't numeric.
3. Creates `output_dir/<prefix>/` if it doesn't already exist (first use of a new prefix).
4. Launches `MaltaMultiDAQ` via `subprocess.Popen`, with `cwd` set to `work_dir` (important: the binary resolves some config file paths — e.g. tap calibration files — relative to its working directory, not relative to wherever the satellite process was started from).
5. Reads the binary's stdout/stderr (merged) line by line for up to 60 seconds, waiting for the line containing `"Start"` (emitted by the binary once all planes are configured and the run has actually started). If the process exits early, or the timeout is reached without seeing `"Start"`, the satellite raises a `RuntimeError` and cleans up.

#### State: `RUN` → `do_run()`

Called repeatedly by the framework while the run is active. On its **first** call for a given run, it launches the online monitor (`run_onlinemonitor.sh <run_number>`) as a background process, non-blocking, and sets an internal flag so it is not relaunched on subsequent calls. Subsequent calls simply skip relaunching.

The monitor script must be self-sufficient regarding its ROOT/software environment (see **Environment note** below) — it should not depend on whatever environment the satellite process happens to have inherited.

#### State: `stopping` → `do_stopping()`

Sends `SIGTERM` (never `SIGKILL` in the first attempt) to the DAQ binary, and waits up to 20 seconds for it to exit cleanly. `SIGTERM` matters because the binary's signal handler closes the ROOT output files properly (`Stop()` → `delete` on each readout module) — a `SIGKILL` would risk corrupting them. If the process doesn't exit within the timeout, it is killed as a last resort and an error is raised. The online monitor process is stopped the same way, with a shorter timeout since it holds no data files.

#### State: `landing`

Clears the process handles.

### Environment note — ROOT/RPATH conflict

The MALTA DAQ setup script and the Corryvreckan/`corry` binary depend on **different, incompatible ROOT installations**:

- `MaltaMultiDAQ` needs whatever ROOT is provided by the DAQ `setup` script.
- `corry` was compiled against `/usr/local/root` (ROOT 6.32.02) and has this path **hard-coded in its RPATH** — `LD_LIBRARY_PATH` cannot override an `RPATH` entry (only a `RUNPATH` can be overridden this way), so forcing another ROOT version via environment variables does not work for `corry`.

As a result, `run_onlinemonitor.sh` **must** start from a clean environment (`unset LD_LIBRARY_PATH`, `unset ROOTSYS`, `unset PYTHONPATH`) before calling `corry`, so that `corry`'s own RPATH correctly resolves to its native ROOT installation. Do not attempt to inject the LCG_104d ROOT/gcc paths into the monitor's environment — that was tried and fails with `undefined symbol` errors due to ABI incompatibility between ROOT 6.28.12 (LCG_104d) and 6.32.02 (`/usr/local/root`, what `corry` was actually built against).

### Known limitations / things to double check

- The satellite assumes `run_id` always has the form `prefix_number`. A run id without an underscore will raise an error.
- The 60-second startup timeout and the "Start" string match are tied to the current stdout wording of `MaltaMultiDAQ` — if that program's logging changes, this detection logic must be updated accordingly.
- The online monitor is launched once per run and not restarted automatically if it crashes mid-run.

---

##Configuration example 
binary_path = "/home/MaltaSW/build/MaltaDAQ/MaltaMultiDAQ"
daq_config =  "/home/MaltaSW/MaltaDAQ/configs/Malta2_W4R1_Initial.txt"
run_test = 600
work_dir = "/home/MaltaSW/MaltaDAQ"
monitor_dir = "/home/MaltaSW/ReadyJuneBeamtest/config/2malta_dut"

## 日本語

### 概要

`MaltaDAQ.py` は、MALTAマルチプレーンDAQシステムをConstellation分散DAQフレームワークに統合するための[Constellation](https://gitlab.cern.ch/constellation)サテライトです。既存のC++バイナリ `MaltaMultiDAQ`（MALTAテレスコープ各プレーンの読み出しを行う、ROOT/TApplicationベースのプログラム）をラップし、ラン中に任意でCorryvreckanベースのオンラインモニターを起動します。これにより、Constellationのテレメトリコンソールとは独立して、リアルタイムのトラッキング/相関プロットを確認できます。

このサテライト自体はDAQのロジックを再実装するものではありません。以下の2つの外部プロセスの**ライフサイクル管理のみ**を担当します。

1. `MaltaMultiDAQ` — MALTAプレーンの設定、ソケットの確立、ROOTファイルへの書き込みを行うC++読み出しバイナリ
2. `run_onlinemonitor.sh` — 利用可能な最良のジオメトリを用いて `corry`（Corryvreckan）を起動し、データをリアルタイムで可視化するbashラッパー

### 動作の流れ

#### 状態: `initializing`

サテライトの設定（TOML）を読み込み、後で使用するパスを保存します。

| 設定キー          | 意味                                                                 |
|-------------------|----------------------------------------------------------------------|
| `binary_path`     | コンパイル済み `MaltaMultiDAQ` 実行ファイルへの絶対パス               |
| `daq_config`      | MALTA読み出し設定ファイルへのパス（`-c` 引数）                         |
| `work_dir`        | DAQバイナリの作業ディレクトリ（設定ファイル内の相対パス、例えばtapキャリブレーションファイルなどが正しく解決されるようにするため） |
| `output_dir`      | ROOTファイルが書き込まれるベースディレクトリ                           |
| `monitor_script`  | オンラインモニターのシェルスクリプト名（デフォルト: `run_onlinemonitor.sh`） |
| `monitor_dir`     | オンラインモニタースクリプトの作業ディレクトリ                         |

#### 状態: `starting` → `do_starting(run_id)`

Constellationのラン識別子は文字列であり、多くの場合 `prefix_number`（例: `edda_71`）の形式ですが、C++バイナリの `-r` 引数には単純な**整数**が必要です。このメソッドは以下を行います。

1. `run_id` を**最後の**アンダースコアで分割（`str.rpartition('_')`）し、プレフィックスとラン番号を分離します。これにより、プレフィックス自体にアンダースコアが含まれる場合でも正しく処理されます。
2. 番号部分を整数に変換します。数値でない場合はエラーを発生させます。
3. `output_dir/<prefix>/` が存在しない場合（新しいプレフィックスの初回使用時）、新規作成します。
4. `subprocess.Popen` で `MaltaMultiDAQ` を起動します。このとき `cwd` を `work_dir` に設定します（重要：バイナリは設定ファイル内の一部のパス — 例えばtapキャリブレーションファイル — をサテライトプロセスが起動された場所ではなく、自身の作業ディレクトリからの相対パスとして解決します）。
5. バイナリの標準出力・標準エラー出力（統合済み）を1行ずつ最大15秒間読み取り、`"Start"` を含む行（全プレーンの設定完了後、ランが実際に開始された際にバイナリが出力する）を待ちます。プロセスが早期終了した場合、またはタイムアウトまでに `"Start"` が見つからない場合、サテライトは `RuntimeError` を発生させ、クリーンアップを行います。

#### 状態: `RUN` → `do_run()`

ランがアクティブな間、フレームワークによって繰り返し呼び出されます。あるランに対する**最初の**呼び出し時にのみ、オンラインモニター（`run_onlinemonitor.sh <run_number>`）をバックグラウンドプロセスとして非同期に起動し、以降の呼び出しで再起動されないよう内部フラグを設定します。以降の呼び出しでは、単に再起動をスキップします。

モニタースクリプトは自身のROOT/ソフトウェア環境について自己完結している必要があります（下記の**環境に関する注意**を参照）。サテライトプロセスがたまたま継承した環境に依存すべきではありません。

#### 状態: `stopping` → `do_stopping()`

DAQバイナリに `SIGTERM` を送信し（最初の試行では決して `SIGKILL` を使いません）、正常終了まで最大20秒待機します。`SIGTERM` が重要な理由は、バイナリのシグナルハンドラがROOT出力ファイルを適切にクローズする（各読み出しモジュールに対して `Stop()` → `delete`）ためです。`SIGKILL` はこれらのファイルを破損させるリスクがあります。タイムアウト内にプロセスが終了しない場合、最終手段としてkillし、エラーを発生させます。オンラインモニタープロセスも同様の方法で停止されますが、データファイルを保持していないため、より短いタイムアウトが設定されています。

#### 状態: `landing`

プロセスハンドルをクリアします。

### 環境に関する注意 — ROOT/RPATHの競合

MALTA DAQのセットアップスクリプトとCorryvreckan/`corry` バイナリは、**互換性のない異なるROOTインストール**に依存しています。

- `MaltaMultiDAQ` はDAQの `setup` スクリプトが提供するROOTを必要とします。
- `corry` は `/usr/local/root`（ROOT 6.32.02）に対してコンパイルされており、このパスは**RPATHにハードコードされています**。`LD_LIBRARY_PATH` は `RPATH` のエントリを上書きできません（`RUNPATH` の場合のみ上書き可能）。そのため、環境変数を使って別のROOTバージョンを強制することは `corry` に対しては機能しません。

その結果、`run_onlinemonitor.sh` は `corry` を呼び出す前に**必ず**クリーンな環境から開始する必要があります（`unset LD_LIBRARY_PATH`、`unset ROOTSYS`、`unset PYTHONPATH`）。こうすることで、`corry` 自身のRPATHが正しくネイティブのROOTインストールを解決します。LCG_104dのROOT/gcc関連パスをモニターの環境に注入しようとしないでください — これは既に試された方法ですが、ROOT 6.28.12（LCG_104d）と6.32.02（`corry` が実際にビルドされた `/usr/local/root`）の間のABI非互換性により、`undefined symbol` エラーで失敗します。

### 既知の制限事項・確認すべき点

- サテライトは `run_id` が常に `prefix_number` の形式であることを前提としています。アンダースコアを含まないラン識別子はエラーになります。
- 15秒の起動タイムアウトと `"Start"` 文字列の一致は、現在の `MaltaMultiDAQ` の標準出力の文言に依存しています。このプログラムのログ出力が変更された場合、この検出ロジックも合わせて更新する必要があります。
- オンラインモニターはランごとに一度だけ起動され、ラン中にクラッシュした場合の自動再起動は行われません。
  * Add the satellite to the [Constellation Satellite Library](https://constellation.pages.desy.de/satellites/index.html) as
    described in the [Constellation Application Developer Guide](https://constellation.pages.desy.de/application_development/intro/listing.html)

---
##Configuration example 
binary_path = "/home/MaltaSW/build/MaltaDAQ/MaltaMultiDAQ"
daq_config =  "/home/MaltaSW/MaltaDAQ/configs/Malta2_W4R1_Initial.txt"
run_test = 600
work_dir = "/home/MaltaSW/MaltaDAQ"
monitor_dir = "/home/MaltaSW/ReadyJuneBeamtest/config/2malta_dut"

