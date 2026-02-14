# **Payments Microservice**

## **Current State**

This microservice integrates with the MercadoPago API and is mainly used to send the frontend the data needed to complete the checkout process.

### **Future Scope**

In the future, this service will fully operate as a **payments microservice** (better aligned with its name), integrating pre-purchase flows (what we currently have for MercadoPago), the purchase flow itself (currently handled by the Bigbox backend in the app **\*\***\*\***\*\***\*\***\*\***\*\***\*\***mercadopago_app**\*\***\*\***\*\***\*\***\*\***\*\***\*\***), and post-purchase flows (updating our database according to what was executed with the corresponding payment gateway API during the purchase process).

### **Project Wiki**

- <https://wiki.bigbox.com.ar/doc/microservicio-de-pagos-h0DnDfH6ks>

### **Prerequisites**

- Python 3.9+
- Docker

### **Installation**

1. Clone the repository:

```bash
git clone git@gitlab.com:bigboxrepo/microservicios/payments.git
cd payments
```

1. It is recommended to create a virtual environment and install dependencies. Even though Docker is used to run the project, a virtual environment is useful when working in an IDE, since it is the simplest way to make dependencies available and access classes, functions, etc.

   Follow these steps:

```bash
python -m venv payments-env
source payments-env/bin/activate # Linux and macOS
.\\payments-env\\Scripts\\activate  # Windows
pip install -r src/requirements.txt
```

### **Run the Microservice**

With PostgreSQL (required for subscriptions):

```bash
docker-compose up -d && docker attach payments
```

To apply database migrations (from host, with venv active and `DATABASE_URL` pointing to Docker PostgreSQL):

```bash
cd src && alembic upgrade head
```

(you can also use only `docker-compose up`, but debugging with IPDB does not work properly that way)

The API will be available at **`http://localhost:8008`**.

### **Environment Variables**

- `MP_PUBLIC_KEY_AR`: MercadoPago public key (pre-purchase / checkout).
- `MP_ACCESS_TOKEN`: MercadoPago access token (subscriptions and webhooks).
- `DATABASE_URL`: PostgreSQL connection (e.g. `postgresql://payments:payments@localhost:5432/payments`). The default is used if not defined.

### **API Endpoints**

- **MercadoPago** (pre-purchase): `/api/v1/mercadopago/` — payment_methods, installments, identification_types, token.
- **Payments (one-time charge)**: `/api/v1/payments/` — POST create payment (token, amount, payment method, payer), GET payment by id.
- **Subscriptions**: `/api/v1/subscriptions/` — plans (list, create, retrieve), subscriptions (create, retrieve, list by user, cancel).
- **Webhooks**: `/api/v1/webhooks/mercadopago` — POST endpoint for MercadoPago notifications (payments and subscriptions).

Endpoints for a combination of **version** and **module** can be found at `/{version}/{module}/`, for example: `/v1/mercadopago/`, `/v1/subscriptions/`.

### **Swagger Docs**

`/docs`

### **Testing**

```bash
pytest src/test
```
