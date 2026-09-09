# LinkedIn Post Draft — Reply Desk

**Built Reply Desk — an AI email assistant where AI drafts, but you still hit send.**

The problem: everyone's inbox is a queue — same greetings, same "thanks, noted", same scheduling back-and-forth. Generative AI is great at writing these, but I didn't want a bot auto-replying to people on my behalf.

So I built a small tool that does three things:

1. **Pulls your unread Gmail** into a clean inbox view
2. **Drafts a short, professional reply** using any AI model you choose — OpenAI, Qwen, Gemini, or OpenRouter (I'm running NVIDIA's Nemotron + Llama models through OpenRouter)
3. **Lets YOU approve, edit, or skip** before anything is sent

Why I like the architecture:

- **Provider-agnostic** — bring your own API key and model. The code speaks the OpenAI-compatible API, so OpenRouter/Qwen/DeepSeek/Ollama all work through one client, and Gemini slots in as a native provider.
- **Privacy-first** — only Gmail's OAuth scope `gmail.modify` (send + mark read, no inbox reading forever), keys live in your local `config.json`, and the API key is masked in the UI.
- **Human in the loop** — no draft is generated until you click "Generate reply", and nothing is sent until you click "Send".

Stack: Python (Flask) + Gmail API + any LLM endpoint, with a small vanilla-JS UI. The terminal version is the same engine with a `y/n/skip` prompt.

Next up: reply scheduling, attachments, and a better email-context window for the prompt.

What's your policy on AI drafting vs. sending? Sentiment is "draft yes, send no" for me.