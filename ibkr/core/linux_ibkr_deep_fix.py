# Test the connection right now
from ib_insync import IB
ib = IB()
try:
    ib.connect(host='::1', port=7497, clientId=1, timeout=10)
    if ib.isConnected():
        print('✅ Connection works!')
        current_time = ib.reqCurrentTime()
        print(f'API test: {current_time}')
        ib.disconnect()
    else:
        print('❌ Connection failed')
except Exception as e:
    print(f'Error: {e}')
