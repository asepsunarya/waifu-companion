import pytest

from waifu.offers.checks import MAX_CHARS, official_link

OFFICIAL = [
    "https://www.anthropic.com/startups",
    "https://anthropic.com/",
    "https://claude.com/pricing",
    "https://WWW.ANTHROPIC.COM/startups",
    "https://www.anthropic.com./startups",
    "https://openai.com/form/researcher-access-program",
    "https://ai.google.dev/pricing",
    "https://cloud.google.com/free",
    "https://startup.google.com/cloud/",
    "https://x.ai/api",
    "https://mistral.ai/pricing",
    "https://console.mistral.ai/",
    "https://ai.meta.com/llama/",
    "https://aws.amazon.com/activate/",
    "https://www.microsoft.com/startups",
    "https://azure.microsoft.com/free/",
    "https://foundershub.startups.microsoft.com/signup",
    "https://education.github.com/pack",
    "https://docs.github.com/en/copilot/get-started/plans",
]
NOT_OFFICIAL = {
    "unknown site": "https://claude-pro-free.example/login",
    "provider name as a subdomain": "https://anthropic.com.evil.example/claim",
    "provider name in another domain": "https://evil-anthropic.com/claim",
    "user name hiding the host": "https://anthropic.com@evil.example/claim",
    "a port": "https://www.anthropic.com:8443/startups",
    # A browser reads the backslash as a slash: this link opens claude-credits.example.
    "backslash before a provider's domain": "https://claude-credits.example\\.anthropic.com/claim",
    "plain http": "http://www.anthropic.com/startups",
    "anyone's Google Site": "https://sites.google.com/view/claude-free-credits",
    "anyone's Google Form": "https://docs.google.com/forms/d/e/1FAIpQL/viewform",
    "Amazon's shop": "https://www.amazon.com/dp/B0EXAMPLE",
    "anyone's GitHub repository": "https://github.com/attacker/free-claude-credits",
    "anyone's gist": "https://gist.github.com/attacker/0123abcd",
    "anyone's Hugging Face Space": "https://huggingface.co/spaces/attacker/claim-free-credits",
    "anyone's published Claude artifact": "https://claude.ai/public/artifacts/0000-attacker",
    "anyone's custom GPT": "https://chatgpt.com/g/g-attacker-free-credits",
    "anyone's shared Gemini chat": "https://gemini.google.com/share/0123abcd",
    "anyone's Google developer profile": "https://developers.google.com/profile/u/attacker",
    "a Cloud Shell tutorial from anyone's repository": (
        "https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/attacker/x"
    ),
    "OpenAI's forum": "https://community.openai.com/t/free-credits/123",
    "Google AI's forum": "https://discuss.ai.google.dev/t/free-credits/123",
    "Mistral's chat app": "https://chat.mistral.ai/chat/0123abcd",
    "Meta's store of other people's apps": "https://www.meta.com/experiences/0123/",
    "Microsoft's forum": "https://answers.microsoft.com/en-us/msoffice/forum/all/free-copilot/abc",
    "Microsoft's community site": "https://techcommunity.microsoft.com/discussions/free/1",
    "Microsoft Learn's questions": "https://learn.microsoft.com/en-us/answers/questions/1/free",
    "not a link": "anthropic.com/startups",
    "unclosed IPv6 bracket": "https://[::1/claim",
}


@pytest.mark.parametrize("url", OFFICIAL)
def test_a_link_on_a_provider_s_own_site_is_official(url):
    assert official_link(url)


@pytest.mark.parametrize("url", NOT_OFFICIAL.values(), ids=NOT_OFFICIAL.keys())
def test_any_other_link_is_not(url):
    assert not official_link(url)


def test_the_texts_a_web_page_could_fill_are_capped():
    assert MAX_CHARS == {"title": 120, "provider": 120, "eligibility": 300, "summary": 300}
