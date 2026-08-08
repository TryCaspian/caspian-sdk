# Serverless Caspian AI agent (AWS Lambda + Bedrock)

Run a Caspian agent with **no always-on server** — it lives in an AWS Lambda,
scales to zero between messages, and thinks with an LLM (Amazon Bedrock Nova).

Normally an agent runs `client.listen()`, a loop that needs a process running
24/7. On serverless platforms there is no such process, so instead the gateway
**pushes** each message to your function and you handle it with
`client.handle_webhook()`:

```
incoming message
   → Caspian gateway signs + POSTs it to your Lambda URL
      → handle_webhook() verifies the signature + dispatches   ← the serverless entry point
         → your @on_message agent → Bedrock Nova → reply
```

`handle_webhook(raw_body, headers, secret)` does three things: verifies the
`x-caspian-signature` HMAC, parses the event, and runs your registered handlers —
then returns. No loop, no connection held open.

## Files

- `handler.py` — the Lambda function (verify → dispatch → LLM reply)

## Deploy

```bash
# 1. Bundle the SDK + deps next to the handler
mkdir build && cd build
pip install caspian-sdk httpx --target .
cp ../handler.py .
zip -qr ../function.zip .
cd ..

# 2. An execution role (Lambda logs + Bedrock)
aws iam create-role --role-name caspian-agent-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name caspian-agent-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name caspian-agent-role --policy-name bedrock \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"bedrock:InvokeModel","Resource":"*"}]}'

# 3. Create the function
aws lambda create-function --function-name caspian-agent \
  --runtime python3.12 --handler handler.lambda_handler --timeout 30 --memory-size 256 \
  --role arn:aws:iam::<ACCOUNT_ID>:role/caspian-agent-role \
  --zip-file fileb://function.zip \
  --environment "Variables={CASPIAN_WEBHOOK_SECRET=<your-secret>,BEDROCK_MODEL=us.amazon.nova-lite-v1:0,BEDROCK_REGION=us-east-1}"

# 4. A public HTTPS endpoint (no server)
aws lambda create-function-url-config --function-name caspian-agent --auth-type NONE
aws lambda add-permission --function-name caspian-agent --statement-id public \
  --action lambda:InvokeFunctionUrl --principal '*' --function-url-auth-type NONE
```

## Try it

```bash
curl -X POST <your-function-url> -H 'content-type: application/json' \
  -d '{"text":"write me a haiku about serverless agents"}'
```

```json
{ "you_said": "...", "agent_reply": "<LLM reply>",
  "model": "us.amazon.nova-lite-v1:0", "handle_webhook_status": "ok" }
```

## Wire it to a real channel (production)

The demo signs its own request so you can `curl` it directly. In production the
**gateway** does the signing — point your project's webhook at the Lambda:

```python
client.set_webhook("https://<your-function-url>", secret="<your-secret>")
```

Now every message on a connected channel is signed and POSTed to your Lambda,
verified by `handle_webhook`, and answered by your agent — fully serverless.
Bedrock is authenticated through the Lambda's IAM role, so there is no LLM API
key to manage.
