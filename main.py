# 本プログラムは、指定された企業の株価チャートとPBRを、
# 自動で米ドル(USD)に換算して表示します（カーソルで日付・価格・PBRをポップアップ）。

import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplcursors
import platform
from datetime import timedelta

# ---------- フォント（日本語環境向け） ----------
try:
    if platform.system() == "Windows":
        plt.rcParams["font.family"] = "Yu Gothic"
    elif platform.system() == "Darwin":  # Mac
        plt.rcParams["font.family"] = "Hiragino Sans"
    else:  # Linux
        plt.rcParams["font.family"] = "IPAexGothic"
except Exception as e:
    print(f"フォント設定エラー: {e}（日本語が化ける可能性あり）")
plt.rcParams["font.size"] = 11
# ------------------------------------------------

# ====== 設定 ======
# 例: 日本株 -> "9107.T", トヨタ -> "7203.T", Apple -> "AAPL", Meta -> "META"
TICKER_CODE = "META"
# ==================

def ensure_series(x):
    """DataFrame/Seriesどちらでも単一Seriesに正規化"""
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            raise ValueError("空のDataFrameをSeries化できません")
        return x.iloc[:, 0]
    return x

def fetch_fx_series(ccy: str, start, end) -> pd.Series:
    """
    現地通貨→USD の換算係数（1 現地通貨あたりの USD）を返す。
    1) CCYUSD=X を試す（例: EURUSD=X）
    2) ダメなら USDCCY=X を取得して逆数（例: USDJPY=X → 1/(USDJPY)）
    """
    if ccy.upper() == "USD":
        # USD→USD は係数1
        idx = pd.date_range(start=start, end=end, freq="D")
        return pd.Series(1.0, index=idx)

    # 1) 直接（CCYUSD=X）
    pair_direct = f"{ccy.upper()}USD=X"
    fx = yf.download(pair_direct, start=start, end=end)
    fx_close = ensure_series(fx["Close"]) if not fx.empty else pd.Series(dtype="float64")
    if not fx_close.empty:
        return fx_close

    # 2) 逆（USDCCY=X）→ 反転
    pair_inverse = f"USD{ccy.upper()}=X"
    fx_inv = yf.download(pair_inverse, start=start, end=end)
    fx_inv_close = ensure_series(fx_inv["Close"]) if not fx_inv.empty else pd.Series(dtype="float64")
    if not fx_inv_close.empty:
        return 1.0 / fx_inv_close

    raise RuntimeError(f"為替レート取得に失敗: {pair_direct} / {pair_inverse}")

def currency_symbol(ccy: str) -> str:
    return {"USD": "$", "JPY": "¥", "EUR": "€", "GBP": "£"}.get(ccy.upper(), ccy.upper())

# ① 企業情報の取得
print(f"{TICKER_CODE} の企業情報を取得しています...")
ticker = yf.Ticker(TICKER_CODE)

# yfinance の info は不安定なことがあるため try/except
try:
    info = ticker.info or {}
except Exception:
    info = {}

short_name = info.get("shortName", TICKER_CODE)
local_ccy = info.get("currency", "USD")  # 例: 'JPY', 'USD'
local_symbol = info.get("currencySymbol", currency_symbol(local_ccy))

# ② 株価データの取得（できるだけ長期）
print(f"{TICKER_CODE} の株価データを取得しています...")
stock = ticker.history(period="max", auto_adjust=False)  # 配当/分割はそのまま
if stock.empty:
    raise RuntimeError("株価データが取得できませんでした。")

# 重複・タイムゾーンの正規化
stock = stock[~stock.index.duplicated(keep="first")]
if stock.index.tz is not None:
    stock.index = stock.index.tz_localize(None)

# ③ PBR（簿価/株主資本）計算
# yfinance の `bookValue` は「1株当たり簿価（Book Value per Share）」が返ることが多い
book_value = info.get("bookValue", None)  # 単位は現地通貨/株
if isinstance(book_value, (int, float)) and book_value > 0:
    stock["PBR"] = stock["Close"] / float(book_value)
else:
    print("⚠️ bookValue が取得できなかったため、PBR計算をスキップしました。")
    stock["PBR"] = pd.NA

# ④ 表示通貨を USD に換算（Close_disp を新設）
display_ccy = "USD"
display_symbol = currency_symbol(display_ccy)

start_date = stock.index.min()
# 終了日は +1 日にして欠損を減らす（Yahooの終端仕様対策）
end_date = stock.index.max() + timedelta(days=1)

try:
    fx_series = fetch_fx_series(local_ccy, start=start_date, end=end_date)
    # 株価日付に合わせて埋める
    fx_series = fx_series.reindex(stock.index).ffill().bfill().astype("float64")
    # 1現地通貨あたりのUSD × 現地通貨建て株価 = USD建て株価
    stock["Close_disp"] = stock["Close"].to_numpy() * fx_series.to_numpy()
    print(f"USDへの換算が完了しました。（{local_ccy}→USD）")
except Exception as e:
    print(f"⚠️ USD換算に失敗したため、現地通貨のまま表示します: {e}")
    display_ccy = local_ccy
    display_symbol = local_symbol
    stock["Close_disp"] = stock["Close"]

# ⑤ グラフ描画
fig, ax = plt.subplots(figsize=(12, 7))
line, = ax.plot(stock.index, stock["Close_disp"], label=f"終値 ({display_ccy})")

ax.set_title(f"{short_name} ({TICKER_CODE}) 株価 & PBR", fontsize=16)
ax.set_xlabel("日付")
ax.set_ylabel(f"終値 ({display_ccy})")
ax.grid(ls="--", alpha=0.6)
fig.autofmt_xdate()
ax.legend()

# ⑥ ホバー注釈（日時・価格・PBR）
cursor = mplcursors.cursor(line, hover=True)

@cursor.connect("add")
def on_add(sel):
    dt = mdates.num2date(sel.target[0]).replace(tzinfo=None)
    # もっとも近いインデックスを取得
    try:
        idx_pos = stock.index.get_indexer([dt], method="nearest")[0]
        if idx_pos == -1:
            return
    except Exception:
        return

    point_date = stock.index[idx_pos]
    date_str = point_date.strftime("%Y年%m月%d日")
    price = stock["Close_disp"].iloc[idx_pos]
    pbr = stock["PBR"].iloc[idx_pos]

    price_text = f"{display_symbol}{price:,.2f}" if pd.notna(price) else "N/A"
    text = f"{date_str}\n終値: {price_text}"
    if pd.notna(pbr):
        text += f"\nPBR: {float(pbr):.2f}倍"

    sel.annotation.set_text(text)
    sel.annotation.get_bbox_patch().set(facecolor="white", alpha=0.9, edgecolor="gray")
    sel.annotation.arrow_patch.set(visible=False)

plt.tight_layout()
plt.show()
