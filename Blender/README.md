# Blender Toolkit

Blender 用の手続き型アセット生成ツール。外部のアセット・画像・AI 生成サービスは使わず、すべてコードで作ります。

- 対象: Blender 5.1 以降（5.2.2 で動作確認）
- `blender_toolkit/generators/can.py` — 飲料缶（350ml / 330ml / 500ml / 250ml_slim / 190ml）
  - 実寸の断面（底のドームと接地リング、ネック、二重巻締め、フタの段差、補強ビード）を回転させて作成
  - リベット、プルタブ（指穴・リベット周りの切り込み）、開け口の溝
  - 胴に円筒 UV、ラベルは Blender 内でレンダーして生成（`label.py`）
- `blender_toolkit/generators/onigiri.py` — おにぎり（三角）
  - 角の丸い三角形をふくらませた本体に、握ったような凹凸
  - ご飯粒を 1 粒ずつ配置（重ならない間隔、つぶれ・反り・太さのばらつき、粒ごとの色の違い）
  - のり：張りのあるシートとして下半分を包む（縁の不揃い、ゆるい波と折り目）
  - 包装（既定）：透明フィルム（角の折り込みしわ）、①の開封帯、①②③のつまみ、正面のラベルシール（`StickerDesign` で商品名・価格・色、`font_path` で日本語）。のりは内フィルム越しにほぼ全面を覆う
  - `packaged=False` で開封した状態
- `blender_toolkit/materials.py` — 手続き型マテリアル（アルミ、印刷ラベル）
- `blender_toolkit/preview.py` — 確認用プレビュー（スタジオ照明、複数アングル）

## 試し方

```
blender -b --factory-startup --python Blender/scripts/render_can.py -- <出力フォルダ> 350ml
```

おにぎりは `scripts/render_onigiri.py`。

出力フォルダに `front.png` などのプレビューと `can_350ml.blend` ができます。

## Python から

```python
from blender_toolkit.generators.can import create_can
from blender_toolkit.label import LabelDesign

create_can('Can', '500ml', label=LabelDesign(brand='NEON COLA', flavor='CHERRY', background='#8a0f1f'))
```

ラベルの文字は Blender 内蔵フォント（英数字のみ）です。日本語は `LabelDesign(font_path='...ttf')` でフォントを指定してください。
