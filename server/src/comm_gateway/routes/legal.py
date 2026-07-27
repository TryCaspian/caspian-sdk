"""Privacy Policy, Terms of Service, and Data Deletion pages.

Meta App Review and app publishing (plus most channel providers) require hosted
legal pages. The gateway serves them itself at /privacy, /terms and
/data-deletion so a developer can paste https://<gateway>/privacy straight into
App Settings without depending on an external site.

The documents are written for Caspian as a multi-channel communication gateway
that processes messages on behalf of the developers who integrate it (i.e. as a
data processor acting on customer instructions). A few entity-specific details
(legal entity name, registered address, governing-law jurisdiction) are marked
so the operator can finalize them; edit COMPANY, JURISDICTION and CONTACT below.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

COMPANY = "Caspian"
CONTACT = "rushant@saasden.club"
JURISDICTION = "the jurisdiction in which Caspian is established"
EFFECTIVE = "July 14, 2026"

_STYLE = """
  :root { color-scheme: light dark; }
  body {font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        max-width: 46rem; margin: 3rem auto 5rem; padding: 0 1.25rem;
        color: #1a1a1a; line-height: 1.65;}
  header {border-bottom: 1px solid #e5e7eb; padding-bottom: 1rem; margin-bottom: 2rem;}
  h1 {font-size: 1.9rem; margin: 0 0 0.35rem;}
  h2 {font-size: 1.2rem; margin-top: 2.25rem; padding-top: 0.5rem;}
  h3 {font-size: 1.02rem; margin-top: 1.4rem;}
  p, li {font-size: 0.98rem;}
  ul {padding-left: 1.25rem;}
  .muted {color: #6b7280; font-size: 0.88rem;}
  .toc {background: #f9fafb; border: 1px solid #eef0f2; border-radius: 0.6rem;
        padding: 1rem 1.25rem; font-size: 0.9rem;}
  .toc ol {margin: 0.25rem 0 0; padding-left: 1.3rem;}
  a {color: #2563eb; text-decoration: none;}
  a:hover {text-decoration: underline;}
  footer {margin-top: 3rem; border-top: 1px solid #e5e7eb; padding-top: 1rem;}
  @media (prefers-color-scheme: dark) {
    body {color: #e5e7eb; background: #0b0d10;}
    header, footer {border-color: #232830;}
    .toc {background: #12151a; border-color: #232830;}
    .muted {color: #9aa4b2;}
    a {color: #7ab0ff;}
  }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\" />"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />"
        f"<title>{title} - {COMPANY}</title><style>{_STYLE}</style></head><body>"
        f"{body}"
        "<footer><p class=\"muted\">"
        f"{COMPANY} &middot; <a href=\"/privacy\">Privacy Policy</a> &middot; "
        "<a href=\"/terms\">Terms of Service</a> &middot; "
        "<a href=\"/data-deletion\">Data Deletion</a> &middot; "
        f"<a href=\"mailto:{CONTACT}\">{CONTACT}</a>"
        "</p></footer></body></html>"
    )


# --------------------------------------------------------------------------- #
# Privacy Policy
# --------------------------------------------------------------------------- #

_PRIVACY_BODY = f"""
<header>
<h1>Privacy Policy</h1>
<p class="muted">Effective date: {EFFECTIVE}</p>
</header>

<p>This Privacy Policy explains how {COMPANY} ("{COMPANY}", "we", "us", or "our")
collects, uses, discloses, and safeguards information in connection with the
{COMPANY} communication gateway and related websites, APIs, dashboards, SDKs, and
integrations (collectively, the "Service"). {COMPANY} provides infrastructure
that lets AI agents send and receive messages across communication channels -
including email, WhatsApp, Instagram, Facebook Messenger, Discord, Telegram,
iMessage, and SMS - on behalf of the developers and organizations that integrate
the Service.</p>

<p>For most message content, {COMPANY} acts as a <strong>data processor</strong>
that processes information solely on the instructions of the developer or
organization (the "Customer") that operates an agent. The Customer is the data
controller for the end-user communications they route through the Service. For
account and billing information about the Customer itself, {COMPANY} acts as a
data controller. Please read this Policy together with our
<a href="/terms">Terms of Service</a>.</p>

<div class="toc">
<strong>Contents</strong>
<ol>
<li><a href="#definitions">Definitions</a></li>
<li><a href="#collect">Information We Collect</a></li>
<li><a href="#use">How We Use Information</a></li>
<li><a href="#legal-bases">Legal Bases for Processing</a></li>
<li><a href="#share">How We Share Information</a></li>
<li><a href="#subprocessors">Subprocessors &amp; Service Providers</a></li>
<li><a href="#retention">Data Retention</a></li>
<li><a href="#transfers">International Data Transfers</a></li>
<li><a href="#security">Data Security</a></li>
<li><a href="#gdpr">Your Rights (GDPR/UK GDPR)</a></li>
<li><a href="#ccpa">California Privacy Rights (CCPA/CPRA)</a></li>
<li><a href="#cookies">Cookies &amp; Tracking</a></li>
<li><a href="#analytics">Analytics</a></li>
<li><a href="#children">Children's Privacy</a></li>
<li><a href="#thirdparty">Third-Party Channels &amp; Links</a></li>
<li><a href="#changes">Changes to This Policy</a></li>
<li><a href="#contact">Contact Us</a></li>
</ol>
</div>

<h2 id="definitions">1. Definitions</h2>
<ul>
<li><strong>Service</strong> - the {COMPANY} gateway, APIs, SDKs, dashboards,
websites, and integrations.</li>
<li><strong>Customer</strong> - the developer or organization that integrates the
Service to operate an AI agent.</li>
<li><strong>End User</strong> - a person who communicates with a Customer's agent
over a connected channel.</li>
<li><strong>Personal Data</strong> - information that identifies or can reasonably
be linked to an identified or identifiable natural person.</li>
<li><strong>Usage Data</strong> - information collected automatically about how the
Service is accessed and used.</li>
<li><strong>Message Data</strong> - the content and metadata of communications
routed through the Service on a Customer's behalf.</li>
<li><strong>Subprocessor</strong> - a third party engaged by {COMPANY} to process
data in order to provide the Service.</li>
</ul>

<h2 id="collect">2. Information We Collect</h2>
<h3>2.1 Information you provide</h3>
<p>When a Customer creates an account or configures the Service, we collect
account details such as name, email address, organization name, API keys, and
channel-connection settings (for example, bot tokens or OAuth authorizations for
connected accounts). Access tokens and channel credentials are stored encrypted
at rest.</p>
<h3>2.2 Message &amp; Channel Data</h3>
<p>To deliver the Service we process the messages routed through the gateway,
including message content, sender and recipient identifiers (such as email
addresses, phone numbers, or platform handles), thread and conversation
identifiers, timestamps, delivery status, and related metadata. We process this
data on the Customer's instructions and only to transmit, receive, normalize, and
report on those communications.</p>
<h3>2.3 Usage Data</h3>
<p>We automatically collect technical information such as IP address, request
timestamps, API endpoints called, device and browser type, and diagnostic logs.
This supports reliability, security, and abuse prevention.</p>
<h3>2.4 Information from third parties</h3>
<p>When a Customer connects a channel, the relevant provider (for example, Meta,
Twilio, or an iMessage relay) may share identifiers and tokens necessary to send
and receive messages on the connected account.</p>

<h2 id="use">3. How We Use Information</h2>
<p>We use information to: (a) provide, operate, and maintain the Service; (b) route
messages between agents and End Users; (c) authenticate Customers and secure
accounts; (d) monitor, troubleshoot, and improve reliability and performance;
(e) detect, prevent, and address fraud, abuse, and security incidents; (f)
communicate with Customers about the Service, including transactional and, where
permitted, product notices; (g) comply with legal obligations; and (h) enforce
our agreements. We do not sell Personal Data, and we do not use message content
to train machine-learning models or for advertising.</p>

<h2 id="legal-bases">4. Legal Bases for Processing</h2>
<p>Where the EU/UK General Data Protection Regulation applies, we rely on the
following legal bases: performance of a contract (to provide the Service);
legitimate interests (to secure, improve, and operate the Service in ways that do
not override individuals' rights); compliance with legal obligations; and consent
where required. For End-User communications processed on a Customer's behalf, the
Customer is responsible for establishing the appropriate legal basis and any
required consents.</p>

<h2 id="share">5. How We Share Information</h2>
<p>We share information only as needed to operate the Service:</p>
<ul>
<li><strong>Channel providers.</strong> Messages are transmitted to the providers
required to reach the intended recipient (see Subprocessors below).</li>
<li><strong>Service providers.</strong> Infrastructure, hosting, storage, and
analytics vendors that process data under contractual confidentiality and
security obligations.</li>
<li><strong>Legal and safety.</strong> Where required by law, legal process, or to
protect the rights, property, or safety of {COMPANY}, our Customers, or others.</li>
<li><strong>Business transfers.</strong> In connection with a merger, acquisition,
financing, or sale of assets, subject to this Policy.</li>
</ul>
<p>We do not sell or rent Personal Data to third parties.</p>

<h2 id="subprocessors">6. Subprocessors &amp; Service Providers</h2>
<p>We engage subprocessors to provide the Service. These may include, without
limitation, communication-channel providers (such as Meta Platforms for WhatsApp,
Instagram, and Messenger; Twilio for SMS and WhatsApp; and third-party relays for
iMessage), cloud infrastructure and email providers (such as Amazon Web
Services), and product-analytics providers. Each subprocessor processes data only
to the extent necessary to perform its services and is bound by appropriate data
protection obligations. A current list of subprocessors is available on request
at <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>

<h2 id="retention">7. Data Retention</h2>
<p>We retain Personal Data only as long as necessary to provide the Service and
for the purposes described in this Policy, unless a longer retention period is
required or permitted by law (for example, to comply with legal obligations,
resolve disputes, or enforce our agreements). Message Data is retained according
to the Customer's configuration and instructions; Customers may request deletion
as described in Section 10 and in our <a href="/data-deletion">Data Deletion</a>
instructions. When data is no longer needed, we delete or anonymize it.</p>

<h2 id="transfers">8. International Data Transfers</h2>
<p>{COMPANY} and its subprocessors may process and store information in countries
other than the one in which you reside, including the United States. Where we
transfer Personal Data across borders, we implement appropriate safeguards, such
as standard contractual clauses or equivalent mechanisms, to protect the data
consistent with applicable law.</p>

<h2 id="security">9. Data Security</h2>
<p>We maintain administrative, technical, and organizational safeguards designed
to protect information, including encryption of channel credentials at rest,
transport encryption (TLS), signed and verified inbound webhooks, access
controls, and audit logging. No method of transmission or storage is completely
secure, and we cannot guarantee absolute security; you use the Service at your
own risk and should protect your API keys and credentials.</p>

<h2 id="gdpr">10. Your Rights (GDPR/UK GDPR)</h2>
<p>If you are located in the EEA, the United Kingdom, or a similar jurisdiction,
you may have the right to access, correct, update, or delete your Personal Data;
to restrict or object to certain processing; to data portability; and to withdraw
consent where processing is based on consent. To exercise these rights, contact
us at <a href="mailto:{CONTACT}">{CONTACT}</a>. Where {COMPANY} processes data on a
Customer's behalf, we will refer requests from End Users to the relevant Customer
and support the Customer in responding. You also have the right to lodge a
complaint with a supervisory authority.</p>

<h2 id="ccpa">11. California Privacy Rights (CCPA/CPRA)</h2>
<p>If you are a California resident, you may have the right to know what Personal
Information we collect and how we use and disclose it, to request deletion or
correction, and to be free from discrimination for exercising your rights.
{COMPANY} does not sell or share Personal Information as those terms are defined
under the CCPA/CPRA. To submit a request, contact
<a href="mailto:{CONTACT}">{CONTACT}</a>. We will verify your request consistent
with applicable law.</p>

<h2 id="cookies">12. Cookies &amp; Tracking</h2>
<p>Our websites and dashboards may use cookies and similar technologies for
essential functionality, security, and to remember preferences. The message
gateway and APIs are not advertising products and do not use tracking cookies for
advertising. You can control cookies through your browser settings; disabling some
cookies may affect dashboard functionality.</p>

<h2 id="analytics">13. Analytics</h2>
<p>We may use privacy-conscious product-analytics tools to understand aggregate
usage and improve the Service. These tools process Usage Data under contractual
data protection obligations and are not used to profile End Users or serve
advertising.</p>

<h2 id="children">14. Children's Privacy</h2>
<p>The Service is intended for businesses and developers and is not directed to
individuals under 18. We do not knowingly collect Personal Data from children. If
you believe a child has provided us Personal Data, contact us and we will take
steps to delete it.</p>

<h2 id="thirdparty">15. Third-Party Channels &amp; Links</h2>
<p>The Service interoperates with third-party communication platforms, each with
its own terms and privacy practices. We are not responsible for the content or
privacy practices of those platforms or of any third-party websites linked from
our properties. Your use of a connected channel is also subject to that
provider's policies.</p>

<h2 id="changes">16. Changes to This Policy</h2>
<p>We may update this Policy from time to time. When we do, we will revise the
"Effective date" above and, where appropriate, provide additional notice.
Continued use of the Service after an update constitutes acceptance of the revised
Policy.</p>

<h2 id="contact">17. Contact Us</h2>
<p>For questions or requests regarding this Policy or your Personal Data, contact
us at <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""


# --------------------------------------------------------------------------- #
# Terms of Service
# --------------------------------------------------------------------------- #

_TERMS_BODY = f"""
<header>
<h1>Terms of Service</h1>
<p class="muted">Effective date: {EFFECTIVE}</p>
</header>

<p>These Terms of Service ("Terms") govern access to and use of the {COMPANY}
communication gateway, APIs, SDKs, dashboards, websites, and integrations
(collectively, the "Service"), and together with our
<a href="/privacy">Privacy Policy</a> form the agreement (the "Agreement")
between {COMPANY} ("{COMPANY}", "we", "us", or "our") and you or the organization
you represent ("you" or "Customer"). By accessing or using the Service, creating
an account, enabling an integration, or clicking to accept, you agree to be bound
by these Terms. If you do not agree, do not use the Service.</p>

<div class="toc">
<strong>Contents</strong>
<ol>
<li><a href="#agreement">The Agreement</a></li>
<li><a href="#definitions-t">Definitions</a></li>
<li><a href="#eligibility">Eligibility</a></li>
<li><a href="#accounts">Accounts &amp; Security</a></li>
<li><a href="#license">The Service &amp; License</a></li>
<li><a href="#acceptable">Acceptable Use</a></li>
<li><a href="#prohibited">Prohibited Uses</a></li>
<li><a href="#channels">Third-Party Channels &amp; Compliance</a></li>
<li><a href="#consent">Consent &amp; Messaging Responsibilities</a></li>
<li><a href="#content">Content &amp; Data Ownership</a></li>
<li><a href="#fees">Fees &amp; Payment</a></li>
<li><a href="#ip">Intellectual Property</a></li>
<li><a href="#feedback">Feedback</a></li>
<li><a href="#privacy-t">Privacy &amp; Data Protection</a></li>
<li><a href="#warranty">Disclaimer of Warranties</a></li>
<li><a href="#liability">Limitation of Liability</a></li>
<li><a href="#indemnity">Indemnification</a></li>
<li><a href="#termination">Term &amp; Termination</a></li>
<li><a href="#service-changes">Changes to the Service</a></li>
<li><a href="#amendments">Amendments to These Terms</a></li>
<li><a href="#law">Governing Law &amp; Disputes</a></li>
<li><a href="#misc">Miscellaneous</a></li>
<li><a href="#contact-t">Contact Us</a></li>
</ol>
</div>

<h2 id="agreement">1. The Agreement</h2>
<p>This Agreement constitutes the entire agreement between you and {COMPANY}
regarding the Service and supersedes prior agreements on that subject. By creating
an account, you agree that we may send you transactional, operational, and
service-related communications; you may opt out of non-essential communications.</p>

<h2 id="definitions-t">2. Definitions</h2>
<p>"Service", "Customer", "End User", and "Message Data" have the meanings given in
our <a href="/privacy">Privacy Policy</a>. "Channel" means a third-party
communication platform (such as email, WhatsApp, Instagram, Messenger, Discord,
Telegram, iMessage, or SMS) that the Service integrates.</p>

<h2 id="eligibility">3. Eligibility</h2>
<p>You must be at least 18 years old and capable of forming a binding contract to
use the Service. If you use the Service on behalf of an organization, you
represent that you are authorized to bind that organization to these Terms.</p>

<h2 id="accounts">4. Accounts &amp; Security</h2>
<p>You are responsible for maintaining the confidentiality of your account
credentials and API keys, for all activity that occurs under your account, and for
promptly notifying us of any unauthorized use. You must provide accurate account
information and keep it current.</p>

<h2 id="license">5. The Service &amp; License</h2>
<p>Subject to these Terms, {COMPANY} grants you a limited, non-exclusive,
non-transferable, revocable license to access and use the Service to build and
operate AI agents that send and receive messages across connected Channels. We may
update, improve, or modify the Service over time.</p>

<h2 id="acceptable">6. Acceptable Use</h2>
<p>You agree to use the Service only for lawful purposes, in compliance with these
Terms, applicable law, and the policies of each Channel you connect. You are
responsible for the content your agent sends and for the conduct of your agents.</p>

<h2 id="prohibited">7. Prohibited Uses</h2>
<p>You must not, and must not permit any agent or End User to: (a) send spam,
unsolicited bulk messages, or content that violates a Channel's consent or rate
rules; (b) transmit unlawful, infringing, harassing, deceptive, or harmful
content; (c) distribute malware or attempt to gain unauthorized access to any
system or data; (d) impersonate any person or entity or misrepresent your
affiliation; (e) circumvent, disable, or interfere with security or usage limits
of the Service or any Channel; (f) use the Service to violate the privacy or
rights of others; or (g) use the Service in any manner that could damage, disable,
or impair the Service or any Channel provider. We may suspend or terminate access
that violates these Terms or a Channel provider's policies.</p>

<h2 id="channels">8. Third-Party Channels &amp; Compliance</h2>
<p>The Service transmits messages through third-party Channels, each governed by
its own terms and policies (including those of Meta, WhatsApp Business, Twilio, and
Apple). Your use of a Channel through the Service is also subject to that
provider's terms, and you are responsible for complying with them, including any
business-verification, template-approval, opt-in, or messaging-window
requirements. {COMPANY} is not responsible for the acts, omissions, availability,
or policies of Channel providers.</p>

<h2 id="consent">9. Consent &amp; Messaging Responsibilities</h2>
<p>You are solely responsible for obtaining and maintaining all consents and legal
bases required to message End Users, and for honoring opt-out and do-not-contact
requests. Certain Channels permit business-initiated messages only via approved
templates and to recipients who have opted in; you must comply with those rules.
You represent that you have the necessary rights and permissions for all messages
you send through the Service.</p>

<h2 id="content">10. Content &amp; Data Ownership</h2>
<p>As between you and {COMPANY}, you retain all rights to your content and the
Message Data you route through the Service. You grant {COMPANY} a limited license
to host, process, transmit, and display that content solely to provide and improve
the Service and as directed by you. We do not use your message content to train
machine-learning models. Our processing of Personal Data is described in the
<a href="/privacy">Privacy Policy</a>.</p>

<h2 id="fees">11. Fees &amp; Payment</h2>
<p>If the Service or any plan is offered for a fee, you agree to pay all applicable
fees as described at the time of purchase. Unless stated otherwise, fees are
non-refundable except as required by law, and we may change fees on reasonable
advance notice. You are responsible for any charges assessed by Channel providers
(for example, per-message or per-conversation fees). Where third-party payment
processors are used, you authorize them to charge your payment method; {COMPANY}
does not store full payment-card details.</p>

<h2 id="ip">12. Intellectual Property</h2>
<p>The Service, including its software, APIs, documentation, and trademarks, is and
remains the property of {COMPANY} and its licensors, and is protected by
intellectual-property laws. Except for the limited license granted above, no rights
are granted to you. You may not use our name, logos, or trademarks without our
prior written permission.</p>

<h2 id="feedback">13. Feedback</h2>
<p>If you provide suggestions, ideas, or feedback about the Service, you grant
{COMPANY} a perpetual, irrevocable, worldwide, royalty-free license to use and
incorporate that feedback without restriction or obligation to you.</p>

<h2 id="privacy-t">14. Privacy &amp; Data Protection</h2>
<p>Our collection and use of information is described in the
<a href="/privacy">Privacy Policy</a>, which is incorporated into these Terms.
Where {COMPANY} processes Personal Data on your behalf, it does so as your
processor and in accordance with your lawful instructions.</p>

<h2 id="warranty">15. Disclaimer of Warranties</h2>
<p>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY
KIND, WHETHER EXPRESS, IMPLIED, OR STATUTORY, INCLUDING IMPLIED WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, AND NON-INFRINGEMENT. WE
DO NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR SECURE, OR
THAT MESSAGES WILL BE DELIVERED, AND WE ARE NOT RESPONSIBLE FOR ACTIONS TAKEN BY
CHANNEL PROVIDERS.</p>

<h2 id="liability">16. Limitation of Liability</h2>
<p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, {COMPANY} AND ITS AFFILIATES WILL NOT BE
LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES,
OR FOR ANY LOSS OF PROFITS, REVENUE, DATA, OR GOODWILL, ARISING OUT OF OR RELATED
TO THE SERVICE. OUR TOTAL LIABILITY FOR ANY CLAIM ARISING OUT OF OR RELATING TO THE
SERVICE WILL NOT EXCEED THE AMOUNTS YOU PAID TO {COMPANY} FOR THE SERVICE IN THE
TWELVE MONTHS PRECEDING THE EVENT GIVING RISE TO THE CLAIM, OR USD 100 IF YOU HAVE
PAID NOTHING.</p>

<h2 id="indemnity">17. Indemnification</h2>
<p>You agree to indemnify and hold harmless {COMPANY} and its affiliates, officers,
and employees from and against any claims, damages, liabilities, and expenses
(including reasonable legal fees) arising out of or related to your use of the
Service, your content, your violation of these Terms, or your violation of any law
or the rights of a third party (including a Channel provider or End User).</p>

<h2 id="termination">18. Term &amp; Termination</h2>
<p>These Terms apply while you use the Service. You may stop using the Service at
any time. We may suspend or terminate your access, with or without notice, if you
violate these Terms, create risk or legal exposure, or if required by a Channel
provider or by law. Upon termination, the licenses granted to you end; provisions
that by their nature should survive (including ownership, disclaimers, limitations
of liability, and indemnities) will survive.</p>

<h2 id="service-changes">19. Changes to the Service</h2>
<p>We may modify, suspend, or discontinue any part of the Service at any time,
including adding or removing Channels or features. We are not liable to you or any
third party for any such modification, suspension, or discontinuation.</p>

<h2 id="amendments">20. Amendments to These Terms</h2>
<p>We may revise these Terms from time to time. When we do, we will update the
"Effective date" above and, where appropriate, provide additional notice.
Continued use of the Service after changes take effect constitutes acceptance of
the revised Terms.</p>

<h2 id="law">21. Governing Law &amp; Disputes</h2>
<p>These Terms are governed by the laws of {JURISDICTION}, without regard to its
conflict-of-laws rules. The courts located in that jurisdiction will have
exclusive jurisdiction over disputes arising out of or relating to these Terms or
the Service, except that either party may seek injunctive relief in any court of
competent jurisdiction to protect its intellectual property or confidential
information.</p>

<h2 id="misc">22. Miscellaneous</h2>
<p>If any provision of these Terms is held unenforceable, the remaining provisions
remain in effect. Our failure to enforce a provision is not a waiver. You may not
assign these Terms without our consent; we may assign them in connection with a
merger, acquisition, or sale of assets. Nothing in these Terms creates a
partnership, agency, or employment relationship. These Terms, together with the
Privacy Policy, are the entire agreement between the parties regarding the
Service.</p>

<h2 id="contact-t">23. Contact Us</h2>
<p>Questions about these Terms may be sent to
<a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""


# --------------------------------------------------------------------------- #
# Data Deletion
# --------------------------------------------------------------------------- #

_DATA_DELETION_BODY = f"""
<header>
<h1>Data Deletion Instructions</h1>
<p class="muted">Effective date: {EFFECTIVE}</p>
</header>
<p>{COMPANY} lets you delete data associated with your account, a connected
channel, or an End-User conversation.</p>
<h2>How to request deletion</h2>
<p>Email <a href="mailto:{CONTACT}">{CONTACT}</a> from the address on file with the
subject line "Data deletion request", and include the account, channel, or
conversation you want removed. To help us verify the request, send it from the
email associated with your account.</p>
<h2>What we delete</h2>
<p>Upon a verified request, we remove the associated connections, stored channel
credentials, and message records from active systems, and instruct our
subprocessors to do the same where applicable. We complete deletion within 30 days
and confirm by reply, except where we are required or permitted by law to retain
certain records (for example, to comply with legal obligations, resolve disputes,
or enforce our agreements).</p>
<h2>Programmatic deletion</h2>
<p>Customers can also delete individual connections through the API
(<code>DELETE /v1/connections/&#123;id&#125;</code>), which removes the connection
and its stored credentials.</p>
"""


@router.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    return _page("Privacy Policy", _PRIVACY_BODY)


@router.get("/terms", response_class=HTMLResponse)
def terms() -> str:
    return _page("Terms of Service", _TERMS_BODY)


@router.get("/data-deletion", response_class=HTMLResponse)
def data_deletion() -> str:
    return _page("Data Deletion", _DATA_DELETION_BODY)
