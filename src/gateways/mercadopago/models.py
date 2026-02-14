from typing import Any

from pydantic import BaseModel


class Bin(BaseModel):
    pattern: str
    exclusion_pattern: str | None = None
    installments_pattern: str


class CardNumber(BaseModel):
    length: int
    validation: str


class SecurityCode(BaseModel):
    mode: str
    length: int
    card_location: str


class Settings(BaseModel):
    bin: Bin
    card_number: CardNumber
    security_code: SecurityCode


class PaymentMethod(BaseModel):
    id: str
    name: str
    payment_type_id: str
    status: str
    secure_thumbnail: str
    thumbnail: str
    deferred_capture: str
    settings: list[Settings]
    additional_info_needed: list[str]
    min_allowed_amount: float | None = None
    max_allowed_amount: float | None = None
    accreditation_time: int
    financial_institutions: dict[str, Any]
    processing_modes: list[str] | None = None

    class Config:
        # example from the mercadopago docs
        schema_extra = {
            "id": "visa",
            "name": "Visa",
            "payment_type_id": "credit_card",
            "status": "active",
            "secure_thumbnail": "https://www.mercadopago.com/org-img/MP3/API/logos/visa.gif",
            "thumbnail": "http://img.mlstatic.com/org-img/MP3/API/logos/visa.gif",
            "deferred_capture": "supported",
            "settings": [
                {
                    "bin": {
                        "pattern": "^(4)",
                        "exclusion_pattern": "^(400163|400176|400178|400185|400199|423808|439267|471233|473200|476332|482481|451416|438935|(40117[8-9])|(45763[1-2])|457393|431274)",
                        "installments_pattern": "^(?!(417401|453998|426398|462437|451212|456188))",
                    },
                    "card_number": {"length": 16, "validation": "standard"},
                    "security_code": {
                        "mode": "mandatory",
                        "length": 3,
                        "card_location": "back",
                    },
                }
            ],
            "additional_info_needed": [""],
            "min_allowed_amount": 0.5,
            "max_allowed_amount": 60000,
            "accreditation_time": 2880,
            "financial_institutions": {},
            "processing_modes": ["aggregator"],
        }


class Issuer(BaseModel):
    id: str
    name: str
    secure_thumbnail: str
    thumbnail: str


class PayerCost(BaseModel):
    discount_rate: float
    installment_amount: float
    installment_rate: float
    installment_rate_collector: list[str]
    installments: int
    labels: list[str]
    max_allowed_amount: float
    min_allowed_amount: float
    payment_method_option_id: str
    recommended_message: str
    reimbursement_rate: float | None = None
    total_amount: float


class InstallmentsInfo(BaseModel):
    agreements: Any | None = None
    issuer: Issuer
    merchant_account_id: Any | None = None
    payer_costs: list[PayerCost]
    payment_method_id: str
    payment_type_id: str
    processing_mode: str

    class Config:
        schema_extra = {
            "payment_method_id": "visa",
            "payment_type_id": "credit_card",
            "issuer": {
                "id": "310",
                "name": "Banco Santander",
                "secure_thumbnail": "https://www.mercadopago.com/org-img/MP3/API/logos/visa.gif",
                "thumbnail": "http://img.mlstatic.com/org-img/MP3/API/logos/visa.gif",
            },
            "processing_mode": "aggregator",
            "merchant_account_id": None,
            "payer_costs": [
                {
                    "installments": 1,
                    "installment_rate": 0,
                    "discount_rate": 0,
                    "reimbursement_rate": None,
                    "labels": ["CFT_0,00%|TEA_0,00%"],
                    "installment_rate_collector": ["MERCADOPAGO"],
                    "min_allowed_amount": 3,
                    "max_allowed_amount": 2500000,
                    "recommended_message": "1 cuota de $ 5.900,00 ($ 5.900,00)",
                    "installment_amount": 5900,
                    "total_amount": 5900,
                    "payment_method_option_id": "1.AQokODllZjQyNjktYjAzMy00OWU1LWJhMWQtNDE0NjQyNTM3MzY4EJaFuevHLg",
                },
            ],
            "agreements": None,
        }


class IdentificationType(BaseModel):
    id: str
    type: str
    min_length: int
    max_length: int

    class Config:
        schema_extra = {
            "id": "DNI",
            "type": "number",
            "min_length": 1,
            "max_length": 8,
        }


class TokenDataInput(BaseModel):
    card_expiration_month: str
    card_expiration_year: str
    card_number: str
    cardholder_name: str
    doc_number: str | None = None
    doc_type: str | None = None
    security_code: str

    class Config:
        schema_extra = {
            "card_expiration_month": "11",
            "card_expiration_year": "2025",
            "card_number": "4509953566233704",
            "cardholder_name": "APRO",
            "doc_number": "12345678",
            "doc_type": "DNI",
            "security_code": "123",
        }


class TokenResponse(BaseModel):
    id: str

    class Config:
        schema_extra = {
            "id": "ff8080814c11e237014c1ff593b57b4d",
        }
