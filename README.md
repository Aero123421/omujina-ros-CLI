# Mujina Assist

`mujina_ros` をリポジトリに内包せず、起動時に自動 clone してセットアップから運用までを日本語で案内する対話型 CLI アプリです。メインCLIは司令塔に徹し、重い処理や常駐系ノードは別ターミナルで起動します。

## 目的

- Ubuntu 24.04 で `mujina_ros` を自動 clone して build する
- RViz 可視化と `--sim` 起動を一つの CLI から実行する
- 実機起動や motor 操作を安全確認付きで実行する
- USB 上の `*.onnx` を検知し、`policy.onnx` の差し替えを簡単にする
- セットアップ・build・SIM・実機起動の実ログを専用ターミナルへ分離する

## 使い方

Ubuntu 24.04 のターミナルで次を実行します。

```bash
git clone <this-repository>
cd ros-mujina-project
./start.sh
```

初回起動時は `.venv` を自動で作り、CLI 本体を起動します。
もし `python3-venv` が入っていない環境では、いったん system Python で起動し、あとから `sudo apt install -y python3-venv` を案内します。

起動後はメニューに従って進めてください。  
セットアップや build、SIM、実機ノード、policy 差し替えは別ターミナルで実行されます。メインCLI側では状態、最近のジョブ、ログの場所を確認できます。

## よく使う流れ

初心者向け:

1. `./start.sh`
2. `初回セットアップ`
3. `可視化`
4. `SIM`
5. 必要なら `ONNX 読み込みテスト`
6. 必要なら `policy 切替`
7. 実機を使う予定ならセットアップ中に `dialout` と `udev` の設定も行う

長いログは別ターミナルに出るので、メインCLIはそのまま開いたままにしておくのがおすすめです。

慣れてきた後のサブコマンド例:

```bash
./start.sh
```

`./start.sh` を入口にしておけば、`.venv` の有無や system Python へのフォールバックを意識せずに使えます。
CLI を直接呼ぶ形は開発者向けのため、通常利用では `./start.sh` を前提にしてください。

## 前提

- Ubuntu Desktop 24.04
- `sudo` が使えること
- ネットワーク接続があること
- 実機操作では IMU / CAN / gamepad が接続されていること

## 主な機能

- 初回セットアップ
- 状態確認
- build
- 可視化
- SIM 起動
- 実機起動
- policy 切替
- ONNX 読み込みテスト
- motor read
- zero position
- ジョブ一覧 / ログ表示

## 実機系の考え方

- `SIM` と `実機` は分けています
- `zero position` と `実機起動` は強い確認付きです
- `policy` を切り替えた直後は、まず `SIM` で確認する前提です
- 実機系は upstream README に寄せて、IMU / mujina_main / joy を別ターミナルで起動します

## テスト方針

このリポジトリ自体は `mujina_ros` を含みません。実行時に `workspace/src/mujina_ros` へ clone します。

VirtualBox で Ubuntu 24.04 VM を用意し、その中で `./start.sh` を実行して確認する想定です。
