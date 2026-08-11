# Provider authorization and policy boundary

This project is intended for legitimate distributed AI compute, not for
obtaining compute by defeating a provider's controls.

## Allowed sources

An adapter may target a local model, a public API, a paid or free tier used by
its account owner, an OpenAI-compatible service with documented access, or a
user-reviewed interface whose Terms of Service do not explicitly prohibit the
intended external use. This includes a no-key public chat UI when its rules do
not prohibit the intended interaction; a page being publicly viewable is not,
by itself, a determination that every use is lawful or risk-free. The operator
must retain whatever authorization the provider requires and is responsible for
the legal interpretation of the terms.

Community bots on Telegram, Discord, or similar platforms are not exempt from
this boundary. Informal ownership or the absence of an obvious contract does
not establish permission to message a bot, use a group resource, or send
private data. Platform Terms of Service, bot instructions, server rules,
privacy expectations, rate limits, and the bot operator's authorization still
matter. Treat these as quarantined candidates until an authorized integration
path is confirmed; do not spam, impersonate users, or infer permission from a
lack of enforcement.

An operator-owned Discord server or Telegram group can provide a cleaner test
environment, but it does not automatically authorize every invited bot. Invite
only bots with an explicit permitted integration path, use a dedicated test
channel, send harmless synthetic prompts first, respect bot and platform rate
limits, and do not use ordinary members' private messages or data as compute.

The coordinator does not autonomously join third-party servers or send
unsolicited direct messages to bots or members in them. A bot being present in a
public server, or accepting a human's manual message, is not authorization for
our controller to use it as a compute endpoint. Discord's developer guidance
also says DMs should generally be initiated by a user action. A cross-server
bot is eligible only through a documented integration path or explicit owner
authorization, with the operator responsible for the server's rules and the
bot's terms.

## Prohibited implementation goals

Do not implement or request code for:

- authentication, paywall, quota, CAPTCHA, or rate-limit bypass;
- stolen credentials, cookies, sessions, API keys, or hidden endpoints;
- scraping or browser automation prohibited by the provider;
- evading safety controls or disguising prohibited traffic;
- rotating browser profiles, clearing cookies, or otherwise resetting state to
  evade a provider's message, quota, or rate limit;
- exploiting a service's bug or misconfiguration to obtain compute;
- sending data to a provider without the required privacy or user consent.

If an adapter stops being legitimately usable, disable it. Do not keep it active
because it is cheap. Candidate discovery must retain provenance, authorization
requirements, applicable terms, and a quarantine state before any future
activation workflow considers it.

## Operator responsibility

Before configuring a provider, read its current Terms of Service, acceptable-use
policy, API documentation, rate limits, privacy policy, and model license. Terms
can differ by account, geography, endpoint, and automation method. This project
does not certify that any third-party provider permits a particular use.

The operator is responsible for compliance, data handling, credentials, costs,
and the consequences of delegated work. The project authors are not responsible
for a user's violation of third-party rules or law. When authorization is
ambiguous, do not automate the provider; ask the provider or choose a clearly
authorized source.
