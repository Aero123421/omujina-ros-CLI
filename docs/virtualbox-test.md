# VirtualBox Test Notes

## 目的

このドキュメントは、Windows ホスト上の VirtualBox を使って Ubuntu 24.04 の新規環境で `Mujina Assist` を検証するときのメモです。

## 想定フロー

1. Ubuntu 24.04 VM を用意する
2. このリポジトリを VM 内へ持ち込む
3. `./start.sh` を実行する
4. `初回セットアップ` を実行する
5. `可視化` と `SIM` を確認する
6. 必要なら USB 上の ONNX を差し替える

## VM 内で最低限確認したいこと

- `python3` がある
- ネットワークが通る
- `sudo` が使える
- Ubuntu が 24.04 である

## 共有方法の例

- GitHub から clone
- VirtualBox の共有フォルダ
- USB メモリ

## 実機系について

VirtualBox 内では、通常は実機デバイス接続まで含めたテストは別途 USB パススルー設定が必要です。

まずは次を優先します。

- 初回セットアップ
- build
- RViz
- `mujina_main --sim`
- policy 差し替え

## 注意

このリポジトリは `mujina_ros` を内包しません。起動後に `workspace/src/mujina_ros` へ自動 clone します。
