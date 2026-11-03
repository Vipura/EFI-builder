"""MacBookAir9,1 SMBIOS Generator."""

class SMBIOSGenerator:
    PRODUCT_NAME = "MacBookAir9,1"
    def generate(self):
        return {"SystemProductName": self.PRODUCT_NAME}
