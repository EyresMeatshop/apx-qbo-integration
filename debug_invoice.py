from qbo_client import QBOClient
import json

q = QBOClient()

data = q.query("SELECT * FROM Invoice WHERE DocNumber = '1022' MAXRESULTS 1")

print(json.dumps(data, indent=2))