from loyverse_client import LoyverseClient

loy = LoyverseClient()

items = loy.get_items()
print(type(items))
print(items)