# DICOM MPR Viewer

Python/Tkinterで自作した、CT DICOMシリーズのMPR（多断面再構成）Viewerです。1つのボリュームからAxial、Coronal、Sagittalを同時表示し、位置、Window Level（WL）、Window Width（WW）を操作できます。

対応範囲は、非圧縮・単一Series・等間隔・標準方向 `ImageOrientationPatient=[1,0,0,0,1,0]` のCTです。対応範囲外のデータは、誤表示を避けるため日本語のエラーを表示して読み込みを中止します。

## 課題要件との対応

| 観点 | 実装内容 |
|---|---|
| R1 | Tkinterの独自GUIにCTスライスを表示 |
| R2 | `ファイル`メニューからDICOMフォルダーまたはZIPを選択 |
| R3 | Axial / Coronal / Sagittalを同時表示 |
| R4 | 各断面に他2面の位置を示す縦線・横線を表示 |
| R5 | 各断面に独立した位置 / WL / WWスライダー（合計9本） |
| R6 | 位置スライダーと3断面の交差線を連動 |
| R7 | 未実装。必要データと実装方針は [`R7_IMPLEMENTATION_PLAN.md`](R7_IMPLEMENTATION_PLAN.md) に整理 |

## 必要環境

- Windows 10/11
- Python 3.11（開発確認: Python 3.11.9）
- Tkinter 8.6（通常のWindows版Pythonに同梱）
- メモリ: 1 GB以上の空き容量を推奨（133枚・512×512のCTはHU変換後約133 MB）

外部パッケージは `requirements.txt` のpydicom、NumPy、Pillowだけです。Viewer本体はネット接続を使いません。初回のパッケージ導入にはインターネット接続が必要です。

## 設計

- `dicom_loader.py`: ZIP/フォルダー読込、DICOMタグ検証、空間位置ソート、HU変換を担当します。正常に全スライスを検証してからGUIの表示データを入れ替えます。
- `mpr.py`: `volume[z,y,x]` を唯一の座標系として、3断面抽出、WL/WW変換、交差線座標、物理アスペクト比変換を純粋関数で提供します。
- `viewer.py`: ファイルメニュー、3パネル、9スライダーとCanvas描画を担当します。位置変更は該当画像と全交差線を更新し、WL/WW変更は該当画像だけを更新します。
- `main.py`: GUIの起動入口です。

画像は読込時に一度だけ `float32` のHUボリュームへ変換し、スライダー操作時にDICOMを再読込しません。

## セットアップと起動（Windows PowerShell）

GitHubの **Code > Download ZIP** からソースを取得して展開し、展開先でPowerShellを開きます。Gitは不要です。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

`py -3.11` が見つからない場合は、python.orgからPython 3.11（64-bit）を導入し、インストーラーの「Add python.exe to PATH」を有効にしてください。

macOS/Linuxでは、Tkinterを利用できるPython 3.11環境で次を実行できます（主な動作対象はWindowsです）。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

## データを開く

- ZIPのまま読む: `ファイル > DICOM ZIPを開く...` から `.zip` を選択します。ZIPはディスクへ展開せず、入れ子フォルダー内の `.dcm` / `.DCM` も検索します。
- 展開済みデータを読む: `ファイル > DICOMフォルダーを開く...` から親フォルダーを選択します。サブフォルダーも再帰検索します。

ファイル名やZIP内の順番ではなく、`ImageOrientationPatient` の法線に `ImagePositionPatient` を射影した位置でスライスを並べます。画素値には各スライスの `RescaleSlope` と `RescaleIntercept` を適用してHUへ変換します。患者名や患者IDは表示・ログ出力しません。

## 画面と操作

- **Axial**: 体を足側/頭側へ移動する断面。内部配列は `volume[z, :, :]`。
- **Coronal**: 体を前後へ移動する断面。内部配列は `volume[:, y, :]`。
- **Sagittal**: 体を左右へ移動する断面。内部配列は `volume[:, :, x]`。
- **位置**: 表示スライスを変更します。赤い縦線と水色の横線が、関連する他の2断面でも直ちに追従します。
- **WL / WW**: その断面だけの濃度中心/幅を変更します。WWは1以上です。
- **ショートカット**: `Ctrl+O` でZIP、`Ctrl+Shift+O` でフォルダーを開きます。
- **ヘルプ**: `ヘルプ > 操作方法` で、画面内から基本操作と交差線の意味を確認できます。

Coronal/Sagittalはスライス間隔と面内PixelSpacingを使って表示比率を補正します。交差線も画像と同じリサイズ・余白変換を使用します。

濃度変換は課題範囲に合わせた単純な線形変換です。`low = WL - WW/2`、`clip((HU-low)/WW, 0, 1) * 255` を8bitグレースケールへ丸めます（厳密なDICOM VOI LUTの半画素規則は使用しません）。

## 自動テスト

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

合成DICOMを使い、3断面抽出、WL/WW、交差線、物理アスペクト比、位置整列、HU変換、フォルダー/ZIP同等性、異常データ拒否を確認します。

手元に課題サンプルZIPがある場合だけ、次のように環境変数を設定すると実データ固有テストも実行されます。ZIP自体はリポジトリに含まれません。

```powershell
$env:DICOM_SAMPLE_ZIP = 'C:\path\to\LIDC-IDRI-0265_CT.zip'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

成功時は最終行が `OK` になります。通常実行ではサンプル固有テスト1件だけが `skipped` です。

## 問題対処

- **Tkinterがない**: Windowsではpython.orgの標準インストーラーでPython 3.11を入れ直します。Linuxではディストリビューションの `python3-tk` 相当を導入します。
- **依存導入に失敗**: Pythonが3.11であることと、仮想環境のPythonで `pip install -r requirements.txt` を実行したことを確認します。
- **ZIPを読み取れない**: ZIPが破損していないこと、拡張子 `.dcm` / `.DCM` の画像が含まれることを確認します。
- **対応外と表示される**: 圧縮DICOM、複数Series混在、斜め/回転撮像、寸法・間隔不一致には対応していません。Seriesごとに分けた標準方向の非圧縮CTを選択してください。
- **空フォルダー**: 選択したフォルダー以下に `.dcm` / `.DCM` があるか確認します。

読み込み失敗時は、すでに正常表示していたデータを保持します。

## 医療画像の取り扱いと制限

課題サンプルを含むDICOM/ZIPはGitHubへ格納しません。`.gitignore` で `*.dcm`、`*.DCM`、`*.zip`、`data/` を除外しています。利用者自身が適切に管理するDICOMをローカルで選択してください。

本アプリは授業課題用であり、診断用途ではありません。任意課題R7（肺領域の3Dボリュームレンダリング）は未実装です。着手に必要な入力、教員へ確認する仕様、最小構成、テスト計画は [`R7_IMPLEMENTATION_PLAN.md`](R7_IMPLEMENTATION_PLAN.md) を参照してください。
