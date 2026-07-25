"""Minimal LinkedIn organization auto-reply agent.

Set CASPIAN_API_KEY and CASPIAN_BASE_URL for the gateway, plus:

    LINKEDIN_ACCESS_TOKEN
    LINKEDIN_ORGANIZATION_URN
    LINKEDIN_POST_URN

The LinkedIn access token must be authorized for the organization that owns the
tracked post. Run this against a gateway with the LinkedIn provider enabled.
"""

import os

from caspian_sdk import CommClient


client = CommClient()
customer = client.create_customer("LinkedIn Demo")
agent = client.create_agent("LinkedIn Auto Reply")
connection = client.connect_linkedin(
    access_token=os.environ["LINKEDIN_ACCESS_TOKEN"],
    organization_urn=os.environ["LINKEDIN_ORGANIZATION_URN"],
    tracked_posts=os.environ["LINKEDIN_POST_URN"],
    customer_id=customer["id"],
    agent_id=agent["id"],
)
print(f"LinkedIn connection active: {connection['address']}")


@client.on_message
def handle(message):
    print(f"Inbound LinkedIn comment: {message.text!r}")
    message.reply(f"Thanks for your comment. You said: {message.text}")


print("Listening for LinkedIn comments (Ctrl+C to stop)")
client.listen()
