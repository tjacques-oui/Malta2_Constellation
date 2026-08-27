# MALTA Power ON/OFF Sequence Scripts

## Overview

This repository provides a set of Python scripts to control and monitor the power sequencing of the **MALTA sensor**. The scripts are organized into two categories: power sequencing control and current consumption monitoring.

---

## 1. Power Sequencing Scripts

These scripts handle the power-up and power-down sequence of the MALTA sensor.

| Script | Description |
|---|---|
| `PowerON_sequence` | Powers up the MALTA sensor following the required sequence. |
| `PowerDOWN_sequence` | Powers down the MALTA sensor following the required sequence. |

---

## 2. Current Monitoring Scripts

These scripts periodically request status information from the power sources and report the current consumption values. Four variants are available, depending on the level of detail and output format required.

| Script                  | Console Output | Log File      | Real-Time Plot | Notes                                         |
|---                      |---             |---	           |---             |---					    |
| `Monitoring_backup`     |        ✅      | ❌            | ❌             | Basic monitoring, console output only.        |
| `Monitoring_Dump`       |        ✅      | ✅ (`./logs`) | ❌             | Same as above, with data saved to a log file. |
| `Monitoring`            |        ✅      | ❌            | ✅             | Same as above, with real-time current curve display. |
| `Monitoring_complete`   |        ✅      | ✅ (`./logs`) | ✅             | **Recommended for production use.** Combines logging and real-time plotting, with full inline documentation. |

> **Note:** `Monitoring_complete` merges the functionality of `Monitoring` and `Monitoring_Dump`. It is the most complete and well-documented version, and should be used as the reference script under real operating conditions.

---

## Log Files

When logging is enabled, output files are automatically saved to the `./logs` directory, with a timestamped filename to avoid overwriting previous sessions:

```
logs/current_log_YYYYMMDD_HHMMSS.txt
```

Each log file contains a header row followed by tab-separated values:

```
Time(s)    IAVdd(A)    IPWell(A)    IDVdd(A)    ISub(A)
```

---

## Usage

1. Run `PowerON_sequence` to power up the MALTA sensor.
2. Run the appropriate monitoring script (`Monitoring_complete` recommended) to track current consumption during operation.
3. Run `PowerDOWN_sequence` to safely power down the MALTA sensor once the test/session is complete.

---

## Requirements

- Python 3.x
- `pyusb`
- `matplotlib` (required for `Monitoring` and `Monitoring_complete`)

---
---

# MALTA 電源 ON/OFF シーケンススクリプト

## 概要

本リポジトリは、**MALTAセンサー**の電源シーケンス制御および監視を行うための一連のPythonスクリプトを提供します。スクリプトは「電源シーケンス制御」と「消費電流モニタリング」の2つのカテゴリーに分かれています。

---

## 1. 電源シーケンススクリプト

MALTAセンサーの電源投入・電源切断シーケンスを制御するスクリプトです。

| スクリプト | 説明 |
|---|---|
| `PowerON_sequence` | 所定のシーケンスに従ってMALTAセンサーの電源を投入します。 |
| `PowerDOWN_sequence` | 所定のシーケンスに従ってMALTAセンサーの電源を切断します。 |

---

## 2. 電流モニタリングスクリプト

これらのスクリプトは、電源装置のステータス情報を定期的に取得し、消費電流の値を表示します。必要な詳細度や出力形式に応じて、4種類のバリエーションが用意されています。

| スクリプト             | コンソール出力 | ログファイル     | リアルタイムグラフ| 備考 |
|---                    |---           |---            |---              |---  |
| `Monitoring_backup`   |     ✅       | ❌            | ❌              | コンソール出力のみの基本モニタリング。 |
| `Monitoring_Dump`     |     ✅       | ✅ (`./logs`) | ❌              | 上記に加え、データをログファイルに保存します。 |
| `Monitoring`          |     ✅       | ❌            | ✅              | 上記に加え、電流値をリアルタイムでグラフ表示します。 |
| `Monitoring_complete` |     ✅       | ✅ (`./logs`) | ✅              | **実運用推奨版。** ログ保存とリアルタイムグラフ表示の両機能を統合し、詳細なコメントを含む完成版です。 |

> **注記:** `Monitoring_complete` は `Monitoring` と `Monitoring_Dump` の機能を統合したスクリプトです。最も完成度が高く、コメントも充実しているため、実運用環境ではこのスクリプトを使用することを推奨します。

---

## ログファイルについて

ログ機能を有効にすると、出力ファイルは自動的に `./logs` ディレクトリに保存されます。ファイル名にはタイムスタンプが付与されるため、過去のセッションを上書きすることはありません。

```
logs/current_log_YYYYMMDD_HHMMSS.txt
```

各ログファイルには、ヘッダー行に続いてタブ区切りの数値データが記録されます。

```
Time(s)    IAVdd(A)    IPWell(A)    IDVdd(A)    ISub(A)
```

---

## 使用方法

1. `PowerON_sequence` を実行し、MALTAセンサーの電源を投入します。
2. 動作中の消費電流を監視するため、適切なモニタリングスクリプト(`Monitoring_complete` 推奨)を実行します。
3. 試験・セッション終了後、`PowerDOWN_sequence` を実行してMALTAセンサーの電源を安全に切断します。

---

## 動作環境

- Python 3.x
- `pyusb`
- `matplotlib`(`Monitoring` および `Monitoring_complete` の実行に必須)
