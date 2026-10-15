"""What an AI offer from the web scan may hold before Cyrene repeats it or opens its link.

The scan reads pages anyone can write. Their words reach her conversation (which can control
devices) and her voice, so the texts a page could fill are capped. A page may also lead the
model to report a lookalike or made-up link for "free Claude credits", so a link opens with
one tap only on a site where nobody but one of the well-known AI providers the scan looks for
publishes; any other link is shown as unverified and never sent as a tap action. Where anyone
can publish on a provider's domain (repositories and Spaces, shared chats and artifacts,
custom GPTs, forums, developer profiles, Google Sites and Forms, the Amazon shop), only the
provider's own hosts there count.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from waifu.notify import is_https_link

# The longest text each field of an AI offer may have, in characters.
MAX_CHARS = {"title": 120, "provider": 120, "eligibility": 300, "summary": 300}

# Domains where only the provider publishes, on the domain and on every host under it.
OFFICIAL_DOMAINS = frozenset(
    {
        # Anthropic (not claude.ai: its shared chats and published artifacts are anyone's)
        "anthropic.com",
        "claude.com",
        # Google (not all of google.com: Sites, Forms, developer profiles, and shared Gemini
        # chats are anyone's)
        "startup.google.com",
        "edu.google.com",
        "blog.google",
        "deepmind.google",
        "gemini.google",  # Google's own .google site (shared chats are on gemini.google.com)
        # xAI
        "x.ai",
        # Meta (not all of meta.com, whose store sells other people's apps)
        "ai.meta.com",
        "llama.com",
        # AWS (not the Amazon shop)
        "aws.amazon.com",
        # Microsoft and GitHub (not github.com, where anyone has repositories)
        "azure.microsoft.com",
        "startups.microsoft.com",
        "education.github.com",
        "docs.github.com",
    }
)

# Hosts where only the provider publishes, though anyone can on other hosts of their domain:
# these hosts count, the others under the domain do not.
OFFICIAL_HOSTS = frozenset(
    {
        # OpenAI (not community.openai.com, a forum, or chatgpt.com, with anyone's GPTs)
        "openai.com",
        "www.openai.com",
        "help.openai.com",  # its help center, written by OpenAI
        # Google (not discuss.ai.google.dev, a forum, or shell.cloud.google.com, which runs
        # anyone's tutorial)
        "ai.google.dev",
        "cloud.google.com",
        # Mistral AI (not chat.mistral.ai, its chat app)
        "mistral.ai",
        "console.mistral.ai",
        "docs.mistral.ai",
        # Microsoft (not answers., techcommunity., or learn.microsoft.com: anyone's posts)
        "microsoft.com",
        "www.microsoft.com",
    }
)


def official_link(url: str) -> bool:
    """Whether `url` opens a provider's own site: a link a notification may open
    (`notify.is_https_link`) whose host is one of OFFICIAL_HOSTS, or one of OFFICIAL_DOMAINS
    or under one."""
    if not is_https_link(url):
        return False
    host = (urlsplit(url).hostname or "").rstrip(".")
    return host in OFFICIAL_HOSTS or any(
        host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_DOMAINS
    )
