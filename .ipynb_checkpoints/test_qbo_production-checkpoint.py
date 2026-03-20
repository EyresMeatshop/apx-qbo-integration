from qbo_client import QBOClient
from config import settings

def main():
    print("Environment:", settings.QBO_ENVIRONMENT)

    qbo = QBOClient()

    try:
        company = qbo.get_company_info()
        print("\nConnected to QBO successfully")
        print(company)
    except Exception as e:
        print("\nFailed to connect to QBO")
        print(str(e))

if __name__ == "__main__":
    main()