import tushare as ts
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

import os
TOKEN = os.environ.get('TUSHARE_TOKEN', 'YOUR_TOKEN_HERE')

def get_stock_data(symbol='000001', exchange='SZ', start_date='20250601'):
    ts.set_token(TOKEN)
    df = ts.pro_api().daily(ts_code=f'{symbol}.{exchange}', start_date=start_date)
    if df.empty:
        raise Exception("获取数据失败，请检查网络连接或token是否有效")
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['daily_return'] = df['close'].pct_change()
    return df

def calculate_indicators(df):
    delta = df['close'].diff(1)
    gain, loss = delta.where(delta > 0, 0), -delta.where(delta < 0, 0)
    df['rsi'] = 100 - (100 / (1 + gain.rolling(14).mean() / loss.rolling(14).mean()))
    
    df['ema_fast'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_fast'] - df['ema_slow']
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['histogram'] = df['macd'] - df['signal']
    
    df['bb_middle'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_middle'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_middle'] - 2 * df['bb_std']
    
    low_list, high_list = df['low'].rolling(9).min(), df['high'].rolling(9).max()
    rsv = (df['close'] - low_list) / (high_list - low_list) * 100
    df['k'] = rsv.ewm(com=2, adjust=False).mean()
    df['d'] = df['k'].ewm(com=2, adjust=False).mean()
    df['j'] = 3 * df['k'] - 2 * df['d']
    return df

def plot_close_price(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['close'], color='blue', linewidth=2)
    ax.set_ylim(df['close'].min() * 0.98, df['close'].max() * 1.02)
    ax.set_title(f'{stock_name} 收盘价走势图', fontsize=14)
    ax.set_xlabel('日期')
    ax.set_ylabel('收盘价 (元)')
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('close_price.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_return_distribution(df, stock_name):
    fig, ax = plt.subplots(figsize=(12, 6))
    returns = df['daily_return'].dropna()
    ax.hist(returns, bins=50, color='blue', alpha=0.7, edgecolor='black')
    ax.axvline(returns.mean(), color='red', linestyle='--', linewidth=2, label=f'均值: {returns.mean():.4f}')
    ax.axvline(returns.mean() + returns.std(), color='green', linestyle='--', label=f'+1σ: {returns.mean()+returns.std():.4f}')
    ax.axvline(returns.mean() - returns.std(), color='green', linestyle='--', label=f'-1σ: {returns.mean()-returns.std():.4f}')
    ax.set_title(f'{stock_name} 日收益率分布')
    ax.set_xlabel('日收益率')
    ax.set_ylabel('频数')
    ax.legend()
    ax.grid(True)
    plt.tight_layout()
    plt.savefig('return_distribution.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_rsi(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['rsi'], color='blue', linewidth=2)
    ax.axhline(70, color='red', linestyle='--', linewidth=1)
    ax.axhline(30, color='green', linestyle='--', linewidth=1)
    ax.fill_between(df['trade_date'], 70, df['rsi'], where=df['rsi'] >= 70, color='red', alpha=0.2)
    ax.fill_between(df['trade_date'], 30, df['rsi'], where=df['rsi'] <= 30, color='green', alpha=0.2)
    ax.set_title(f'{stock_name} RSI指标图', fontsize=14)
    ax.set_xlabel('日期')
    ax.set_ylabel('RSI值')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('rsi_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_macd(df, stock_name):
    fig, ax1 = plt.subplots(figsize=(14, 7))
    ax1.plot(df['trade_date'], df['macd'], color='blue', linewidth=2, label='MACD')
    ax1.plot(df['trade_date'], df['signal'], color='red', linewidth=2, label='Signal')
    ax1.bar(df['trade_date'], df['histogram'], color='gray', alpha=0.5, label='Histogram')
    ax1.axhline(0, color='black', linestyle='--', linewidth=1)
    ax1.set_title(f'{stock_name} MACD指标图', fontsize=14)
    ax1.set_xlabel('日期')
    ax1.set_ylabel('MACD值')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('macd_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_bollinger(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['bb_upper'], color='red', linestyle='--', linewidth=1, label='上轨')
    ax.plot(df['trade_date'], df['bb_middle'], color='orange', linewidth=1, label='中轨')
    ax.plot(df['trade_date'], df['bb_lower'], color='green', linestyle='--', linewidth=1, label='下轨')
    ax.plot(df['trade_date'], df['close'], color='blue', linewidth=2, label='收盘价')
    ax.fill_between(df['trade_date'], df['bb_lower'], df['bb_upper'], color='gray', alpha=0.1)
    ax.set_title(f'{stock_name} 布林带指标图', fontsize=14)
    ax.set_xlabel('日期')
    ax.set_ylabel('价格 (元)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('bollinger_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_kline(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    x = range(len(df))
    
    for i in x:
        color = 'red' if df.loc[i, 'close'] >= df.loc[i, 'open'] else 'green'
        ax.plot([i, i], [df.loc[i, 'low'], df.loc[i, 'high']], color=color, linewidth=1)
        ax.fill_between([i-0.3, i+0.3], df.loc[i, 'open'], df.loc[i, 'close'], color=color)
    
    ax.set_xticks(range(0, len(df), 10))
    ax.set_xticklabels([df.loc[i, 'trade_date'].strftime('%Y-%m-%d') for i in range(0, len(df), 10)], rotation=45)
    ax.set_title(f'{stock_name} K线图', fontsize=14)
    ax.set_xlabel('日期')
    ax.set_ylabel('价格 (元)')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('kline_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_kdj(df, stock_name):
    fig, ax = plt.subplots(figsize=(16, 8))
    
    valid_df = df.dropna(subset=['k', 'd', 'j'])
    
    y_min = min(valid_df['j'].min(), 0) - 10
    y_max = max(valid_df['j'].max(), 100) + 10
    
    ax.plot(valid_df['trade_date'], valid_df['k'], color='blue', linewidth=2, label='K线')
    ax.plot(valid_df['trade_date'], valid_df['d'], color='red', linewidth=2, label='D线')
    ax.plot(valid_df['trade_date'], valid_df['j'], color='green', linewidth=2, label='J线')
    
    ax.axhline(80, color='red', linestyle='--', linewidth=1, alpha=0.7)
    ax.axhline(20, color='green', linestyle='--', linewidth=1, alpha=0.7)
    ax.axhline(50, color='gray', linestyle=':', linewidth=1, alpha=0.5)
    
    ax.fill_between(valid_df['trade_date'], 80, valid_df['k'], where=valid_df['k'] >= 80, color='red', alpha=0.1)
    ax.fill_between(valid_df['trade_date'], 20, valid_df['k'], where=valid_df['k'] <= 20, color='green', alpha=0.1)
    
    ax.set_ylim(y_min, y_max)
    ax.set_title(f'{stock_name} KDJ指标图', fontsize=14)
    ax.set_xlabel('日期')
    ax.set_ylabel('KDJ值')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_yticks([0, 20, 50, 80, 100])
    
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('kdj_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def main():
    stock_name = '恒瑞医药'
    symbol = '600276'
    exchange = 'SH'
    
    print(f"获取{stock_name}({symbol}.{exchange})股票数据...")
    df = get_stock_data(symbol, exchange, '20250601')
    
    print(f"获取成功，共{len(df)}条记录")
    print(f"日期范围：{df['trade_date'].min()} ~ {df['trade_date'].max()}")
    df.to_csv(f'{stock_name}.csv', index=False, encoding='utf-8-sig')
    print(f"数据已保存为 {stock_name}.csv")
    
    print("\n数据诊断：")
    print(f"缺失值：{df.isnull().sum().sum()}个")
    print(f"日均收益率：{df['daily_return'].mean():.4f}")
    print(f"收益率标准差：{df['daily_return'].std():.4f}")
    print(f"最大涨幅：{df['daily_return'].max():.4f}")
    print(f"最大跌幅：{df['daily_return'].min():.4f}")
    
    print("\n计算技术指标...")
    df = calculate_indicators(df)
    
    print("绘制可视化图表...")
    plot_close_price(df, stock_name)
    plot_return_distribution(df, stock_name)
    plot_rsi(df, stock_name)
    plot_macd(df, stock_name)
    plot_bollinger(df, stock_name)
    plot_kline(df, stock_name)
    plot_kdj(df, stock_name)
    
    print("\n分析完成！")

if __name__ == '__main__':
    main()