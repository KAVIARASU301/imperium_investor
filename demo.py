import plotly.graph_objects as go
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
import sys

# Sample data
import pandas as pd
df = pd.DataFrame({
    'Date': ['2025-06-10', '2025-06-11', '2025-06-12'],
    'Open': [100, 105, 103],
    'High': [110, 108, 107],
    'Low': [95, 102, 100],
    'Close': [107, 103, 106]
})

fig = go.Figure(data=[go.Candlestick(
    x=df['Date'],
    open=df['Open'],
    high=df['High'],
    low=df['Low'],
    close=df['Close']
)])
fig.update_layout(xaxis_rangeslider_visible=False)

html = fig.to_html(include_plotlyjs='cdn')

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        view = QWebEngineView()
        view.setHtml(html)
        self.setCentralWidget(view)

app = QApplication(sys.argv)
window = MainWindow()
window.show()
sys.exit(app.exec())
