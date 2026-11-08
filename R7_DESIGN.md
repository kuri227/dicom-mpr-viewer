# 観点7 肺領域3Dボリュームレンダリング設計書

## 1. 目的

第5回で抽出した肺領域の連続TIFFマスクを、現在のCTボリュームへ安全に対応付け、3Dボリュームレンダリングとして表示する。既存のR1〜R6を変更・退行させず、誤った位置合わせのマスクを表示しないことを最優先とする。

## 2. 対象範囲

### 実装する

- CT読込後に、ファイルメニューから多ページTIFF肺マスクを選択
- TIFFのshape、画素型、ラベル、空マスク、全画面マスクを検証
- CTとのスライス順を正順・逆順から安全に自動判定
- CTのvoxel spacingを用いた肺マスクの3Dボリュームレンダリング
- 独立VTKウィンドウでの回転、ズーム、平行移動と、Tk操作ウィンドウからの視点リセット
- マスク読込状態と判定結果の表示
- エラー時に既存MPR表示を維持

### 実装しない

- 肺領域の抽出処理そのもの
- TIFF以外のマスク形式
- 左右肺の自動分離
- MPR交差線と3Dカメラの連動
- 診断・計測機能
- TIFFまたはDICOMのGitHub同梱

## 3. 確認済み入力仕様

対象ファイル `連続TIFF.tif` を実測した結果:

| 項目 | 値 |
|---|---|
| ページ数 | 133 |
| 各ページ | 512×512 |
| Pillow mode / dtype | `L` / `uint8` |
| ラベル値 | 0（背景）、255（肺領域） |
| 非空ページ | 99 |
| 肺領域voxel数 | 2,790,512 |
| 面内解像度 | 1.28 pixel/mm = 0.78125 mm/pixel |
| CT shape | `(133,512,512)` |
| CT spacing | `(2.5,0.78125,0.78125)` mm、順序はz,y,x |

TIFFページ順をそのままCTへ対応させた場合、マスク内のHU<-300割合は57.9%だった。ページ順を逆転した場合は99.7%となり、代表断面の重ね合わせでも左右肺野と一致した。したがって、この入力はZ方向がCTの格納順と逆である。

## 4. 採用技術

- TIFF読込: 既存依存のPillow `ImageSequence.Iterator`
- 配列・検証: 既存依存のNumPy
- 3D描画: `vtk==9.7.2`
- ウィンドウ統合: `vtkRenderWindowInteractor` をTkinterの `after` で非同期処理
- Volume mapper: `vtkGPUVolumeRayCastMapper`

VTKはPython 3.11 Windows x86-64 wheelが提供され、GPU ray castingを備える。Windows wheelにはTk埋め込み用DLLが同梱されないため、VTKの独立RenderWindowをTkinterから管理する。Qt、PyVista、Webサーバーは追加しない。

## 5. ファイル構成

```text
dicom-mpr-viewer/
├── lung_mask.py           # TIFF読込、検証、順序判定
├── volume3d.py            # NumPy→VTK変換、3Dウィンドウ
├── viewer.py              # メニュー、状態、エラー処理の結線
├── tests/
│   ├── test_lung_mask.py  # 合成TIFFと順序判定
│   └── test_volume3d.py   # vtkImageData変換
└── R7_DESIGN.md
```

## 6. データモデル

```python
@dataclass(frozen=True)
class LungMask:
    mask: NDArray[np.bool_]       # shape [z,y,x]、CTと同じ順序
    source_name: str              # basenameのみ。患者情報は保持しない
    reversed_z: bool              # TIFFページ順を反転したか
    air_fraction: float           # 選択した順序のHU<-300割合
    voxel_count: int
```

マスクはメモリ上でboolとして保持する。CTのspacingは既存 `DicomVolume.spacing_zyx` を唯一の物理間隔情報とし、TIFFの解像度タグだけで3D間隔を決めない。

## 7. TIFF検証と順序判定

### 検証

1. TIFFが2ページ以上ある。
2. 全ページのshapeが同一で、CTの `(z,y,x)` shapeと一致する。
3. 全画素値が2種類以下で、背景0を含む。
4. `value > 0` を肺領域とする。
5. 肺領域が0 voxelでない。
6. 肺領域が全voxelの95%以上を占めない。

### Z順序の安全な自動判定

正順マスクと逆順マスクについて、次を計算する。

```text
air_fraction = count(CT_HU < -300 within mask) / count(mask)
```

- 高い方が0.80以上
- 高い方と低い方の差が0.10以上

この2条件を満たす場合だけ高い方を採用する。満たさない場合は位置合わせを断定できないため、3D表示せず明確なエラーを出す。X/Y反転や回転は自動推定しない。

## 8. NumPyからVTKへの変換

入力は `mask[z,y,x]`、VTK dimensionsは `(x,y,z)` とする。NumPy配列をC-orderでflattenするとxが最速軸になるため、次の対応でコピーする。

```text
vtk dimensions = (x_count, y_count, z_count)
vtk spacing    = (x_spacing, y_spacing, z_spacing)
vtk scalars    = uint8(mask) * 255, flattened in C-order
```

原点は `(0,0,0)` とする。絶対患者座標ではなく、形状と物理比率の正しい表示が目的である。

## 9. 3Dレンダリング

- mapper: `vtkGPUVolumeRayCastMapper`、composite blend
- scalar 0: 完全透明
- scalar 255: 肺を示す赤系色、不透明度0.35
- interpolation: linear
- shading: on
- background: MPR Canvasと同系統の濃紺
- camera: `ResetCamera()` 後に形状を確認しやすい斜め視点を初期表示

GPU volume renderingが初期化できない場合は、例外をGUIで表示し、MPR Viewer本体は継続する。

## 10. GUI設計

### ファイルメニュー

CT読込後に利用する項目を追加する。

```text
ファイル
├── DICOM ZIPを開く...
├── DICOMフォルダーを開く...
├── 肺マスクTIFFを開いて3D表示...
└── 終了
```

CT未読込時に肺マスクを選択した場合は、「先にCTを開いてください」と表示する。CTを再読込した時は、旧CTに対応する肺マスクと3Dウィンドウを破棄する。

### 3Dウィンドウ

- VTK表示ウィンドウのタイトル: `肺領域 3D Volume Rendering`
- Tk操作ウィンドウ: 入力ファイル名、voxel数、Z反転の有無、整合率
- 操作ウィンドウ: `視点をリセット`、マウス操作説明、`閉じる`
- VTK初期サイズ: 800×700、画面内へ収める

既存の3断面レイアウトへ4枚目を押し込まず、3Dを独立したVTKウィンドウにする。Tkinter側の小さな操作ウィンドウから視点リセットと終了を行う。これによりR1〜R6の視認性と900×600対応を維持する。

## 11. 状態遷移

```text
未読込
  └─ CT成功 → MPR表示
       ├─ 肺TIFF成功 → LungMask保持 → 3D表示
       ├─ 肺TIFF失敗 → エラー表示 → MPR表示を維持
       └─ 別CT読込成功 → 既存3Dを閉じる → 新MPR表示
```

ファイル選択キャンセルは状態を変更しない。

## 12. エラー処理

ユーザー向けメッセージは、問題と回復方法を含める。

- ページ数・shape不一致: CTと同じ枚数・寸法のTIFFを選ぶよう案内
- 多値画像: 0/1または0/255の二値マスクを案内
- 空/過大マスク: 抽出結果を確認するよう案内
- 順序判定不能: 同一CT由来か、順序・回転を確認するよう案内
- VTK未導入: `pip install -r requirements.txt` を案内
- GPU描画失敗: GPUドライバーとVTK環境を確認するよう案内

例外時に患者名、ID、フルパスは表示・ログ出力しない。

## 13. テスト計画

### 自動テスト

- 正順・逆順マスクを正しく選択
- shape不一致、多値、空、全画面マスクを拒否
- 判定差が小さい曖昧なマスクを拒否
- NumPy→VTK変換のdimensions、spacing、scalar range、voxel数
- 既存19テストの回帰
- 実TIFF: 133ページ、shape一致、逆順選択、air fraction約0.997

### GUI動作確認

- メニューから実TIFFを選択
- 3Dウィンドウに左右肺が表示
- 回転、ズーム、平行移動、視点リセット
- 3Dウィンドウを閉じてもMPR操作可能
- CT再読込で旧3Dが閉じる
- 900×600で既存MPRレイアウトが維持

## 14. セキュリティ・公開方針

- `.tif` / `.tiff` を `.gitignore` へ追加
- DICOM、TIFF、スクリーンショット、患者情報をcommitしない
- GUIとテストログへ患者タグを出さない
- TIFFはローカル読込のみで、ネットワーク送信しない

## 15. 完了条件

- 第5回の連続TIFFが安全にCTへ対応付けられる。
- 肺領域が物理spacingを反映して3D表示される。
- マウスで3D操作でき、視点リセットが機能する。
- 不正マスクを誤表示しない。
- R1〜R6と全自動テストが成功する。
- READMEだけで導入・操作・制限を再現できる。
- GitHub追跡対象にDICOM/TIFF/venv/個人情報を含めない。
