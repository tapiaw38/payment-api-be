class MercadopagoAPIException(Exception):
    def __init__(self, response):
        self.status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {}
        self.error_code = body.get("code", body.get("error", ""))
        self.error_msg = body.get("message", str(response.text)[:200])
        self.full_body = body
        super().__init__(
            f"MercadoPago API error: {self.status_code} {self.error_code} {self.error_msg} | body={body}"
        )
