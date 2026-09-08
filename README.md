# **Payments Microservice**

## **Current State**

This microservice integrates with the MercadoPago API and is mainly used to send the frontend the data needed to complete the checkout process.

### **Future Scope**

In the future, this service will fully operate as a **payments microservice** (better aligned with its name), integrating pre-purchase flows (what we currently have for MercadoPago), the purchase flow itself (currently handled by the nymia backend in the app **\*\***\*\***\*\***\*\***\*\***\*\***\*\***mercadopago_app**\*\***\*\***\*\***\*\***\*\***\*\***\*\***), and post-purchase flows (updating our database according to what was executed with the corresponding payment gateway API during the purchase process).

### **Project Wiki**

- <https://wiki.nymia.com.ar/doc/microservicio-de-pagos-h0DnDfH6ks>

### **Prerequisites**

- Python 3.9+
- Docker

### **Installation**

1. Clone the repository:

```bash
git clone git@gitlab.com:nymiarepo/microservicios/payments.git
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
- `MP_WEBHOOK_SECRET`: Secret from MercadoPago Webhooks. Required to receive notifications; unsigned notifications are rejected.
- `DATABASE_URL`: PostgreSQL connection (e.g. `postgresql://payments:payments@localhost:5432/payments`). The default is used if not defined.

### **API Endpoints**

- **MercadoPago** (pre-purchase): `/api/v1/mercadopago/` — payment_methods, installments, identification_types, token.
- **Payments (one-time charge)**: `/api/v1/payments/` — POST create payment (token, amount, payment method, payer), GET payment by id.
- **Subscriptions**: `/api/v1/subscriptions/` — plans, subscriptions, monthly billing cycles and deferred cancellation reconciliation.
- **Webhooks**: `/api/v1/webhooks/mercadopago` — signed MercadoPago notifications. Delivery IDs are persisted to make retries idempotent.

### Recurring billing

A `Plan` defines catalog pricing. A `BillingCycle` records immutable monthly usage: active students, unit price, minimum price and final amount. Create the cycle before the next charge, then call `POST /subscriptions/billing-cycles/{cycle_id}/schedule`; this updates only that teacher's MercadoPago `preapproval` amount. Never change a shared plan to reflect one teacher's student count.

Set `MP_SUBSCRIPTION_WEBHOOK_URL` to this service's public HTTPS endpoint. It is sent to MercadoPago for every subscription; caller-supplied notification URLs are ignored.

Run `python -m jobs.reconcile_cancellations` daily from Cron, Kubernetes CronJob or systemd timer. Equivalent protected endpoint: `POST /subscriptions/reconcile-cancellations`. It cancels subscriptions previously marked `at_period_end` only after their paid period expires.

The academic backend can ask `GET /subscriptions/subscriptions/user/{user_id}/entitlement` before granting paid features. This is service-to-service API, protected by `PAYMENTS_API_KEY` in production.

Endpoints for a combination of **version** and **module** can be found at `/{version}/{module}/`, for example: `/v1/mercadopago/`, `/v1/subscriptions/`.

### **Swagger Docs**

`/docs`

### **Testing**

```bash
pytest src/test
```
