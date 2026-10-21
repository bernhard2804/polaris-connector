# Evaluation einer lokalen Agent-Runtime

Stand: 2026-09-20

Status: Entscheidungsgrundlage und PoC-Scope. Keine bestehende Automation wird hiermit migriert.

## Kurzentscheidung

**Entscheidung B: ein vorhandenes Framework mit einer dünnen eigenen
Abstraktionsschicht.**

Für den ersten, isolierten PoC wird **Pydantic AI 2.x plus Pydantic AI Harness
0.32.x** verwendet. Die Auswahl ist nicht mit einer späteren Festlegung auf
Pydantic verbunden: dauerhafte Polaris-Daten bleiben framework-neutral, die
Runtime wird hinter kleinen Ports betrieben, und jede Session muss in ein
versioniertes JSONL-Exportformat exportierbar sein.

Microsoft Agent Framework ist die beste konservative Alternative, falls die
Harness-0.x-API zu häufig bricht. DeepSeek Harness ist das beste
Architekturvorbild für Sessions und Cache-Stabilität, aber wegen seines
ausdrücklichen Developer-Preview-/Security-Status noch kein geeigneter
Produktivanker. LangGraph + Deep Agents ist funktional vollständig, für den
vorliegenden Einzelrechner-Use-Case aber schwerer und stärker an LangChain- und
LangSmith-Konzepte gekoppelt als nötig.

Die Arbeitshypothese wird damit bestätigt, aber präzisiert:

> So wenig eigenes Harness wie möglich. Eigener Code besitzt nur stabile
> Verträge für Konfiguration, Policy, Memory-Retrieval, normalisierte Metriken
> und Export. Loop, Tool Dispatch, Resume, Compaction, MCP und OTel bleiben im
> gewählten Framework.

## 1. Ist-Architektur

### 1.1 Komponenten und Datenflüsse

```text
ChatGPT im Firefox
  -> WebExtension
  -> ~/Downloads/polaris-inbox/*.json
  -> systemd --user path/service
  -> Polaris Bridge
  -> <Drive-Projekt>/raw/chatgpt/{messages,conversations}/*.json

Projektquellen
  raw/chatgpt/messages/*.json
  inbox/{mail,documents,notes}/
  -> Polaris Curator (Timer oder CLI)
  -> codex exec --ephemeral --sandbox read-only
  -> striktes JSON-Schema + lokale Validierung
  -> atomarer Merge
  -> knowledge.md / decisions.md / open-loops.md
  -> .polaris/curator-state.json

Gemeinsamer Maschinenkontext
  ~/.config/ai-agents/AGENTS.md
  ~/Work/AI_HANDOFF.md
  ~/.config/ai-agents/system-history/
  -> von lokalen Agenten gelesen, nicht von einer gemeinsamen Runtime verwaltet
```

### 1.2 Zuständigkeiten

| Komponente | Zuständigkeit | Keine Zuständigkeit |
|---|---|---|
| Firefox-Extension | selektiver Export neuer ChatGPT-Nachrichten | Modellaufrufe, Kuratierung |
| Bridge | Validierung, Projekt-Mapping, append-only Rohablage | Agent Loop, Memory-Entscheidung |
| Curator | Batchbildung, Extraktion/Klassifikation, Merge in Langzeitwissen | interaktive Agent-Session, Resume |
| Curator-State | bereits verarbeitete Message-IDs und Dateisignaturen | vollständiger Arbeitsverlauf |
| Machine Memory | dauerhafte, kuratierte Regeln und Maschinenwissen | rekonstruierbare Session-Historie |
| Google-Drive-Projekte | Rohdaten und kuratiertes Projektwissen | Runtime-interne Checkpoints |
| Omarchy-Widget/systemd | Bedienung und zeitgesteuerte Ausführung | Agent-Orchestrierung |

### 1.3 Vorhandene State- und Session-Mechanismen

- Die Bridge schreibt Nachrichten kollisionsfrei und append-only als einzelne
  JSON-Dateien. Das ist gute Provenance, aber kein Agent-Session-Log.
- `curator-state.json` ist ein atomar ersetzter Verarbeitungssnapshot. Er wird
  erst nach erfolgreichem Merge aktualisiert und ermöglicht idempotentes
  Catch-up. Er kann keinen Agentenlauf rekonstruieren oder mitten in einem
  Tool-Zyklus fortsetzen.
- Der Curator startet für jedes Work Item einen neuen, ephemeren
  `codex exec`-Prozess. Zwischen diesen Aufrufen existiert keine Runtime-Session.
- Der Curator liest derzeit bis zu 50.000 Zeichen aus `knowledge.md`,
  `decisions.md` und `open-loops.md` in jeden Prompt. Das ist für den begrenzten
  Batch-Use-Case vertretbar, aber kein allgemeines on-demand Memory Retrieval.

### 1.4 Provider, Logging und Tools heute

- `provider` und `runtime` stehen bereits in `curator.yaml`, sind im
  Ausführungspfad aber noch keine echte Abstraktion. `llm.py` ruft ausschließlich
  `codex exec` auf.
- `model` und `fallback_model` sind konfigurierbar, jedoch nur innerhalb der
  Codex-Runtime.
- Das Logging enthält technische Metadaten, Batchstatus und eine aus stderr
  geparste Gesamt-Tokenzahl. Es gibt keine getrennten Input-/Cached-/Output-
  Tokens, Kosten oder OpenTelemetry-Spans.
- Es gibt keine MCP-Integration. Vorhandene Tools sind Python-Code,
  `pdftotext` und die isolierte Codex-CLI.
- Der Curator nutzt `codex exec --ephemeral -s read-only`; das ist eine gute
  lokale Sicherheitsgrenze, aber noch keine allgemeine Policy-Matrix.

### 1.5 Überschneidungen mit einem Harness

Nicht duplizieren:

- Curator-Budgetierung und Quellenfortschritt sind fachliche Batchlogik und
  bleiben bestehen.
- Ein allgemeines Harness darf dagegen Agent Loop, Tool Calling, Retry,
  Run-Resume, Compaction und Telemetrie besitzen.
- `knowledge.md`, `decisions.md` und `open-loops.md` bleiben Langzeit-Memory.
  Sie dürfen nicht zum Session-Store eines Frameworks werden.
- Bridge-Rohdaten bleiben Source-Provenance. Sie dürfen nicht in eine
  Framework-Message-Datenbank verschoben werden.

## 2. Anforderungen und harte Grenzen

Die Runtime muss folgende Grenzen einhalten:

1. Provider/Modelle werden über Profile ausgewählt, nicht in fachlicher Logik.
2. Session und Memory sind getrennte Lebenszyklen.
3. Modell-sichtbarer Verlauf ist rekonstruierbar; ein Crash darf bestätigte
   Schritte nicht still verlieren.
4. Stabile Systeminstruktionen und Tool-Schemas stehen am Promptanfang;
   dynamischer Kontext wird angehängt oder on demand geladen.
5. Framework-Persistenz ist zulässig, wenn ein versionierter offener Export und
   eine getestete Exit-Route existieren.
6. Persistente Agentenobjekte dürfen nicht zum Format von Projektwissen oder
   Machine Memory werden.
7. Security wird außerhalb des Prompts erzwungen. Ein Prompt ist keine Sandbox.
8. Kein autonomes Modellrouting, kein Daemon, kein Multi-Agent-System und keine
   Migration bestehender Automationen im ersten Schritt.

## 3. Kandidaten

### 3.1 DeepSeek Harness

DeepSeek Harness besitzt die sauberste Session-Architektur der untersuchten
Kandidaten. Eine Session ist ein append-only Log typisierter `SessionEvent`s;
History, Transcript, Forks, Telemetrie und Persistenz sind Projektionen daraus.
Compaction blendet ersetzte Einträge aus, löscht sie aber nicht. Der JSONL-
Backend-Vertrag verlangt zusammenhängende Sequenzen, einen Flush als
Durability-Barriere und genau einen Writer je Session. Modell-sichtbar bedeutet
explizit: im Log rekonstruierbar.

Weitere Stärken sind stabile serverqualifizierte MCP-Toolnamen,
provider-/modellbezogene Adapter, Session-Forks, ein austauschbarer Agent Loop
und die Trennung von Sandboxmodus und Approval Policy. Die Dokumentation nennt
KV-Cache-Auswirkungen pro Komponente ausdrücklich.

Gegen eine Auswahl jetzt sprechen:

- Das Projekt bezeichnet sich als Developer Preview und kündigt Breaking
  Changes an.
- Der Safety-Hinweis sagt ausdrücklich, dass es nicht security-auditiert oder
  produktionsreif ist.
- Die Sandbox deckt Dateieffekte ab; Netzwerk- und Prozesssichtbarkeit liegen
  außerhalb dieses Vokabulars.
- Die OTel-Integration exportiert derzeit primär Session-Telemetrie als OTel
  Logs, nicht den vollständigen standardisierten Trace-/Metric-Vertrag, den wir
  benötigen.
- Node/Cordis führt einen zweiten technologischen Schwerpunkt neben dem
  bestehenden Python-Stack ein.

Fazit: **Architekturvorbild und möglicher späterer Kandidat, derzeit nicht
Produktivbasis.**

Primärquellen: [Repository und Preview-Hinweis](https://github.com/deepseek-ai/deepseek-harness),
[Architektur](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md),
[Session-Modell](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/README.md),
[Persistenz](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-persistence/README.md),
[MCP](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md),
[Sandbox](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/sandbox/README.md),
[Safety](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md).

### 3.2 Microsoft Agent Framework / Harness Agent

Microsoft Agent Framework 1.19.0 ist MIT-lizenziert und als stabil klassifiziert.
Es bietet einen gemeinsamen Agent-Vertrag, Python/.NET/Go, Middleware,
ContextProvider, Agent Skills, MCP, Tool Approvals, Workflows mit Checkpoints und
eingebaute OpenTelemetry-Instrumentierung. OpenAI, Anthropic, Foundry und
OpenAI-kompatible Endpoints sind direkte Provider; Ollama kann über seinen
OpenAI-kompatiblen Endpoint angesprochen werden.

Seine Schwäche für unseren Kernfall ist die Session-Semantik: Die offizielle
Empfehlung lautet, das vollständige `AgentSession` als opakes JSON zu speichern
und mit derselben Agent-/Providerkonfiguration zu restaurieren. Das ist robust
für Resume, aber weder ein kanonisches append-only Audit-Log noch ein guter
langfristiger Portabilitätsvertrag. Ein zusätzliches Audit-History-Provider-
Muster ist möglich, müsste aber von uns zusammengesetzt werden. Compaction in
Python ist noch experimentell und gilt nur für frameworkverwaltete History.

Fazit: **beste Fallback-Option bei Priorität API-Stabilität/OTel; weniger passend
für offene event-gesourcte Sessions.**

Primärquellen: [Repository](https://github.com/microsoft/agent-framework),
[Provider](https://learn.microsoft.com/en-us/agent-framework/agents/providers/),
[OpenAI-kompatible Endpoints/Ollama](https://learn.microsoft.com/en-us/agent-framework/hosting/self-hosting/openai-endpoints),
[Session Storage](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/storage),
[Tool Approval](https://learn.microsoft.com/en-us/agent-framework/agents/tools/tool-approval),
[Observability](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/observability/README.md),
[Compaction](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction),
[Python 1.19.0](https://github.com/microsoft/agent-framework/releases/tag/python-1.19.0).

### 3.3 LangGraph + Deep Agents

Deep Agents baut einen vollständigen Harness als Middleware-Stack auf LangChain
und LangGraph: Filesystem, Shell/Sandbox, Summarization, Skills, Permissions,
Human-in-the-loop, Subagents und Memory Store. LangGraph-Checkpointer liefern
durable execution, Thread-Resume und Time Travel. `DeltaChannel` vermeidet für
lange Threads quadratisches Checkpointwachstum der Messages.

Das ist technisch stark, aber für den ersten Polaris-Use-Case mehr
Orchestrierungsfläche als erforderlich. Persistenz ist Graphstate/Checkpoint,
nicht das DeepSeek-artige kanonische Ereignislog. Observability ist in der
Dokumentation und im Produkt stark auf LangSmith ausgerichtet; OTel kann ergänzt
werden, ist aber nicht die neutrale Default-Erfahrung. `deepagents` bleibt
pre-1.0 und hat 2026 sichtbare Breaking Changes. Die vielen LangChain-
Integrationspakete erhöhen Upgrade- und Dependency-Fläche.

Fazit: **richtig für explizite Graph-Workflows oder komplexe Multi-Agent-
Orchestrierung, für den kleinen Runtime-Kern derzeit zu schwer.**

Primärquellen: [Architektur](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md),
[Deep Agents](https://github.com/langchain-ai/deepagents),
[Changelog](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/CHANGELOG.md),
[Profiles](https://docs.langchain.com/oss/python/deepagents/profiles),
[Ollama-Integration](https://docs.langchain.com/oss/python/integrations/providers/ollama/).

### 3.4 Pydantic AI + Pydantic AI Harness

Pydantic AI stellt den typisierten Agent Loop, Provideradapter, MCP,
Tool-Approvals, Usage-Normalisierung und OTel bereit. Das offizielle Harness
ergänzt komponierbare Capabilities für Filesystem, Shell, Skills, Compaction,
Memory, Subagents, Cache-Bust-Messung und `StepPersistence`.

Für Polaris besonders passend:

- Python passt zum vorhandenen Connector/Curator.
- Provider sind Modellstrings/Adapter; OpenAI, Anthropic, DeepSeek, Ollama und
  LiteLLM werden unterstützt.
- `RequestUsage` normalisiert Cache Reads/Writes providerübergreifend.
- `Instrumentation` erzeugt OTel-Spans für Run, Model Request und Tool Call nach
  GenAI Semantic Conventions.
- `StepPersistence` schreibt append-only Step Events und Tool-Effect-Records,
  vollständige Snapshots an sicheren Grenzen sowie Parent-/Conversation-
  Lineage. File-, SQLite- und Mongo-Backends implementieren ein `StepStore`
  Protocol.
- `continue_run` erlaubt Wiederaufnahme in einem neuen Prozess; unaufgelöste
  Tool-Effekte bleiben als `unknown_after_crash` sichtbar.
- `WarnOnCacheBusts` wertet die echten normalisierten Providerwerte aus, statt
  Cachetreffer zu schätzen.

Wesentliche Grenzen:

- Harness 0.32 ist pre-1.0; Minor Releases dürfen APIs ändern.
- `StepPersistence` ist noch kein vollständiger branchbarer Event-Store.
  Snapshots, nicht das Ereignislog, sind heute die Resume-Quelle. Das offene
  Vorhaben #321 beschreibt die fehlenden Parent-Event-IDs, replaybaren
  Compaction-Events und Leaf-Pointer.
- Vollständige Snapshots je Step können ohne Retention O(n²)-Speicher erzeugen.
- `Coder` führt Shellbefehle standardmäßig auf dem Host aus. Für Polaris darf
  nur eine bewusst zusammengesetzte Capability-Liste verwendet werden; Coder
  wird nicht pauschal aktiviert.
- Filesystem-Scope ist keine Shell-Sandbox. Systemgrenzen müssen weiter durch
  Omarchy/Codex-ähnliche Sandbox- oder Containermechanismen erzwungen werden.

Fazit: **bester Fit für den PoC, solange exakt gepinnt, minimal komponiert und
mit getesteter Export-/Exit-Strategie.**

Primärquellen: [Pydantic AI](https://github.com/pydantic/pydantic-ai),
[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness),
[StepPersistence](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/step-persistence.md),
[OTel-Instrumentation](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/instrumentation.md),
[Ollama](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/ollama.md),
[MCP](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/mcp.md),
[Cache-Bust-Messung](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/warn-on-cache-busts.md),
[Event-Sourcing-Lücke #321](https://github.com/pydantic/pydantic-ai-harness/issues/321).

### 3.5 LiteLLM

LiteLLM ist ein Model Gateway, kein Agent Harness. Es vereinheitlicht viele
Provider hinter OpenAI-kompatiblen APIs und ergänzt Budgets, Keys, Routing,
Kosten und zentrale Observability. Es kann Cache-Control-Marker einsetzen und
cache-affin routen.

Es sollte **nicht** in den ersten PoC aufgenommen werden:

- Pydantic AI deckt die vier zunächst benötigten Provider bereits direkt ab.
- Ein zusätzlicher Proxy erschwert die Zuordnung provider-nativer Features und
  Usage-Felder.
- LiteLLMs eigener Response Cache ist nicht unser Ziel. Wir wollen
  Provider-Prompt-Caching messen, keinen eigenen Ergebnis-Cache betreiben.
- Aktuelle offene Issues zeigen, dass MCP-Majorversionen und Cache-affines
  Routing bewegliche Integrationsflächen bleiben.

LiteLLM wird später optional sinnvoll, wenn Polaris zentrale Schlüssel,
Budgets, Failover oder mehrere Agent-Runtimes gemeinsam verwalten soll. Die
Runtime-Profile müssen direkte Adapter und Gateway-Routen gleich behandeln.

Primärquellen: [Repository](https://github.com/BerriAI/litellm),
[Architektur](https://github.com/BerriAI/litellm/blob/main/ARCHITECTURE.md),
[Prompt Caching](https://docs.litellm.ai/docs/completion/prompt_caching),
[MCP-2.x-Tracking](https://github.com/BerriAI/litellm/issues/35306).

### 3.6 Weitere Kandidaten (Kurzprüfung)

- OpenAI Agents SDK: gute Sessions/Tracing, aber die dauerhafte Kernabstraktion
  wäre unnötig OpenAI-zentriert.
- Anthropic Claude Agent SDK: starker Coding-Agent, aber Anbieterbindung und
  keine neutrale Session-Datenschicht.
- Google ADK: offen und multi-provider-fähig, aber für diesen Stack weniger
  passend als die Python-Kandidaten und stärker auf Google-Deploymentpfade
  ausgerichtet.
- AutoGen/Semantic Kernel: Microsoft bündelt die Zukunft im Agent Framework;
  ein neuer Aufbau auf den Vorgängern schafft Migrationsschuld.
- Agno/CrewAI/AutoGPT: keine strukturellen Vorteile gegenüber den vier
  Hauptkandidaten bei offener Session-Persistenz, OTel und konservativer
  Integration.

## 4. Technischer Vergleich

Legende: „ja“ bedeutet im Framework vorhanden; „Adapter“ bedeutet ohne Änderung
der Agentenlogik, aber über ein Zusatzpaket/einen kompatiblen Endpoint; „teilw.“
benennt eine strukturelle Einschränkung.

| Kriterium | DeepSeek Harness | Microsoft Agent Framework | LangGraph + Deep Agents | Pydantic AI + Harness |
|---|---|---|---|---|
| Provider-Agnostik | ja, LLM-Seam/pi-ai | ja, ChatClient-Abstraktion | ja, LangChain Models | ja, Model-Abstraktion |
| OpenAI | ja | ja | ja | ja |
| Anthropic | ja | ja | ja | ja |
| DeepSeek | direkt + kompatible Route | OpenAI-kompatibler Adapter | Integration/kompatibler Adapter | direkt/Provideradapter |
| Ollama/lokal | offiziell unterstützt | native/kompatibler Endpoint | `langchain-ollama` | `ollama:` / `OllamaModel` |
| MCP | stdio + HTTP, stabile Namen | lokal/hosted, OTel-Kontext | Adapterpaket | lokal/nativ, Capability |
| Agent Loop | austauschbarer Default Loop | Framework Agent/Harness Agent | LangChain Agent auf LangGraph | typisierter Pydantic-AI-Loop |
| Persistente Sessions | JSONL-Backend | serialisiertes `AgentSession` | Checkpointer | StepStore + Snapshots |
| Restart/Resume | ja | ja | ja | ja |
| Append-only/Event Sourcing | **kanonisch** | nein, Snapshot; Audit ergänzbar | Checkpoint-Historie, nicht kanonisch | Step Events ja; Resume aus Snapshot |
| Crash-/Tool-Effekt-Sicherheit | Flush + Attempt Events | Workflow-Checkpoint abhängig | Node-Checkpoint; Idempotenz App-Sache | Tool-Effect-Ledger, unknown-after-crash |
| Context Management | Projektion + Plugins | ContextProvider | sehr stark | Capabilities/Processors |
| Compaction | log-erhaltend | Python experimentell | stark, Summarization | mehrere Strategien, native Optionen |
| Prompt-Cache-Freundlichkeit | explizites Designziel | möglich, weniger explizit | Profile; wechselnde Middleware beachten | Usage-normalisiert + CacheBust-Warner |
| Tooling | Plugin Registry, Shell/FS | Function/MCP/Skills | umfangreich | Toolsets/MCP/Capabilities |
| Subagents | Fork/Lineage | Workflows/Orchestrations | eingebaut | Capability; Parent Run Lineage |
| Permissions | Sandbox + Approval, Dateieffekte | Approval + Middleware; Sandbox separat | Regeln + HITL/Sandboxes | Approval + Toolsets; OS-Sandbox separat |
| Network/Secrets als harte Grenze | teilw. | anwendungsabhängig | backendabhängig | anwendungsabhängig |
| OTel | OTel Logs, begrenzt | **stark, eingebaut** | LangSmith-first, OTel ergänzbar | **OTel GenAI nativ** |
| Cached-Token-Metrik | Providerdaten nutzbar | providerabhängig | providerabhängig | normalisierte Read/Write Tokens |
| Long-Term-Memory-Anbindung | MCP/Plugin | ContextProvider | Store/Backend | Capability/Toolset/Dependency |
| Self-hosting/offline | ja | ja | ja | ja |
| Abhängigkeiten | Node/Cordis, modular | mittel, Multi-SDK | hoch | mittel, selektive Extras |
| API-Stabilität | Developer Preview | Core GA/stabil | pre-1.0 | Core stabiler, Harness 0.x |
| Maintenance 2026 | sehr aktiv | sehr aktiv | sehr aktiv | sehr aktiv |
| Vendor Lock-in | gering im Code, neues Ökosystem | mittel durch MS-Typen/Foundry-Nähe | mittel durch LangChain-Typen | gering-mittel durch Pydantic-Typen |
| Omarchy-/Polaris-Fit | architektonisch hoch, operativ früh | hoch | mittel | **hoch** |

## 5. Cache-Architektur

Wir bauen keinen KV- oder Response-Cache. Der Sessionaufbau soll
providerseitige Prefix-Caches begünstigen:

```text
[stabile Harness-/Policy-Instruktionen]
[stabile, deterministisch sortierte Tool-Schemas]
[append-only sichtbare Session-History]
[Checkpoint/Compaction-Epoch]
[dynamischer Retrieval-Kontext und neue Nutzereingabe]
```

Regeln:

1. Keine Zeitstempel, zufälligen IDs oder wechselnde Toolreihenfolge im Prefix.
2. Memory-Treffer werden spät und explizit angehängt, nicht in den statischen
   Systemtext umsortiert.
3. Toolsets bleiben während einer Session-Epoch stabil. Eine Änderung beginnt
   eine neue Epoch und wird als Event/Metrik erfasst.
4. Compaction erzeugt einen neuen Checkpoint/Epoch; der alte Verlauf bleibt im
   Session-Store/Export, auch wenn er nicht mehr Modellkontext ist.
5. Pro Modellaufruf werden `input_tokens`, `cache_read_tokens`,
   `cache_write_tokens`, `output_tokens`, Latenz und Kosten (wenn bekannt)
   erfasst. Cache Hit Rate ist `cache_read / total_input`, wobei die
   providerspezifische Definition von `total_input` normalisiert werden muss.

Providerdetails dürfen nicht in die fachliche Session-Logik sickern:

- OpenAI liefert cached input in Usage-Details; stabile Prefixe ab
  providerabhängiger Mindestlänge sind entscheidend.
- Anthropic unterscheidet uncached, cache creation und cache read; explizite
  oder automatische Breakpoints sind Adapterkonfiguration.
- DeepSeek cached überlappende Prefixe automatisch und liefert Hit/Miss-Tokens.
- Ollama liefert je nach lokalem Modell/Backend nicht zwingend vergleichbare
  Cache-Usage. Fehlende Daten sind `null`, nicht `0`.

Primärquellen: [OpenAI Prompt Caching](https://openai.com/index/api-prompt-caching/),
[Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache/),
[OTel GenAI Attribute Registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).

## 6. Risiken

| Risiko | Folge | Gegenmaßnahme |
|---|---|---|
| Harness-0.x-API ändert sich | PoC-Code bricht | exakte Pins, Renovation nur mit Tests, schmaler Adapter |
| Step-Snapshots wachsen quadratisch | Storage wächst bei langen Runs | Retention bewusst setzen; Eventexport behalten; messen |
| Framework-Objekte werden Dauerformat | Exit wird teuer | kein Frameworktyp in Memory/Tasks/Business-Daten; JSONL-Exportvertrag |
| Tool-Approval wird mit Sandbox verwechselt | Hostschaden möglich | OS-Grenze + Policy; PoC nur read-only Tools |
| Modellwechsel verändert Tool-Calling | gleiche Logik verhält sich anders | Providerprofile und Contract-/E2E-Tests |
| Gateway glättet Providerfeatures falsch | Cache-/Usage-Verlust | direkte Adapter als Referenzpfad behalten |
| Compaction zerstört Cache-Hit | höhere Kosten/Latenz | Epoch-Metrik und A/B-Cache-Test |
| OTel zeichnet Inhalte/Secrets auf | Datenabfluss | Content Capture standardmäßig aus; lokale Exportziele |
| Session und Memory vermischen sich | unkontrollierte Langzeitdaten | separate Ports, Namespaces und Schreibautorität Curator |

## 7. Empfohlene Zielarchitektur

Die vorhandenen Bridge-/Curator-Verzeichnisse bleiben unverändert. Eine spätere
Integration soll **keinen neuen Top-Level-Monolithen** erzeugen, sondern eine
Runtime-Grenze neben `bridge/` und `curator/`:

```text
polaris-chatgpt-connector/
  bridge/                 # unverändert: Source Intake
  curator/                # unverändert: Memory Writer
  runtime/                # erst nach erfolgreichem PoC
    core/
      config.py           # Task-Klasse -> Profil -> Modellroute
      contracts.py        # Session-/Memory-/Policy-/Metric-Dataclasses
      export.py           # offenes, versioniertes JSONL
    adapters/
      pydantic_ai.py      # einziger Framework-Adapter
      memory.py           # read-only Retrieval aus vorhandenem Memory
      policy.py           # Zuordnung zu Tool-Capabilities/Approvals
    integrations/
      mcp.py              # Konfiguration, keine eigene MCP-Implementierung
      curator.py          # Übergabe von Session-Kandidaten, kein Direkt-Write
```

Der PoC bleibt vorerst unter `experiments/agent-runtime-poc/`, damit diese
Zielstruktur nicht vor einer erfolgreichen Evaluierung produktiv vorgetäuscht
wird.

### Eigentumsgrenzen

| Eigene dünne Schicht besitzt | Framework besitzt |
|---|---|
| stabile Profilnamen (`cheap/local`, `standard`, ...) | Agent Loop und Tool-Call-Protokoll |
| provider-neutrale Config und ModelRoute | Provider-SDKs und Streaming |
| Policy-Klassen und Mapping auf Fähigkeiten | konkrete Toolsets, MCP-Client |
| Memory-Retrieval-Port, read-only | Context Hook/Capability-Ausführung |
| normalisiertes Session-Summary/Metric-Schema | OTel-Spans und rohe Provider-Usage |
| offenes JSONL-Exportformat + Migrationswerkzeug | native Resume-Persistenz/StepStore |
| Curator-Handoff-Vertrag | keine Entscheidung über Langzeit-Memory |

### Was wir ausdrücklich nicht selbst bauen

- keinen Agent Loop
- keinen Tool-Call-Parser oder MCP-Client
- keinen eigenen Graph-/Multi-Agent-Orchestrator
- keinen KV-/Prompt-Cache
- keine allgemeine Compaction Engine
- keinen eigenen Tracer oder Tokenzähler, wenn Provider/Framework Daten liefern
- keinen Modellrouter mit autonomer Auswahl
- keine neue Memory-Datenbank
- keinen produktiven Daemon im ersten Schritt

## 8. Modellrouting

Nur deklaratives Routing:

```text
Task-Klasse -> Agent-Profil -> Modellklasse -> konkrete Route
```

Beispiel:

| Task-Klasse | Profil | Modellklasse | konfigurierbare Route |
|---|---|---|---|
| lokale Dateisichtung | reader | cheap/local | `ollama:<model>` |
| Curator-nahe Extraktion | curator | standard | direkter Provider |
| Coding | coder | coding | direkter Provider oder Gateway |
| Recherche | researcher | strong | direkter Provider oder Gateway |

Keine Runtime darf anhand selbst erzeugter Heuristiken Provider oder Kostenklasse
wechseln. Ein späterer Router bekommt einen eigenen Vertrag und Audit-Events.

## 9. Telemetrievertrag

OTel ist die primäre Live-Telemetrie. Daneben existiert eine kleine lokale,
provider-neutrale Session-Summary:

```text
session_id, parent_session_id, run_id, agent_role,
provider, model, started_at, ended_at, steps, tool_calls,
input_tokens, cached_input_tokens, cache_write_tokens, output_tokens,
cache_hit_rate, cost, duration_ms, compactions,
memory_reads, memory_writes, errors, exit_status
```

Regeln:

- Unbekannte Werte sind `null`.
- Tokenfelder werden nie aus Zeichenlängen erfunden.
- Kosten werden mit Preisquelle und Preisversion gespeichert.
- Prompt-/Toolinhalte sind in OTel standardmäßig deaktiviert.
- Session-Export und Telemetrie sind verschiedene Zwecke: Export ist Audit/Exit,
  OTel ist Diagnose/Metrik.

## 10. Migrationspfad

1. Dokumentation und ADR festschreiben.
2. Isolierten read-only PoC mit Fake-/Testmodell vollständig testen.
3. Lokalen Ollama-Pfad testen, sobald ein funktionsfähiges Tool-Calling-Modell
   installiert und der Dienst aktiv ist.
4. Cloud-Pfad mit separatem API-Key testen; bestehende Codex-CLI-Anmeldung nicht
   zweckentfremden.
5. Resume, Crash-Grenze, JSONL-Export und OTel-Spans verifizieren.
6. Cache-A/B-Test nur auf Providern mit echten Usage-Feldern ausführen.
7. Erst nach Review entscheiden, ob `experiments/` in `runtime/` überführt wird.
8. Curator zunächst nur als read-only Memory-Quelle anbinden; Schreiben bleibt
   beim bestehenden Curator.
9. Bestehende Claude-/Codex-/Antigravity-Workflows bleiben parallel und
   unverändert, bis ein nachweisbarer Vorteil vorliegt.

## 11. Exit-Strategie

Ein Frameworkwechsel gilt als beherrschbar, wenn:

1. Profile, Policies und Memory Ports keine Frameworktypen exportieren.
2. Jede abgeschlossene Session als `polaris.session.v1` JSONL exportiert ist.
3. Das Exportformat Events, Metriken, Modellroute und Parent-Lineage enthält,
   aber keine pickled Python-Objekte.
4. Ein Golden-Test dieselbe Fixture-Session aus Framework-Persistenz exportiert.
5. Fachliche Agentenanweisungen und Toolverträge unabhängig vom Adapter liegen.
6. Der Ersatzadapter mindestens Start, Resume, Tool-Aufruf, Usage und Export
   implementiert, bevor die alte Runtime entfernt wird.

Die native Persistenz bleibt während eines Frameworkbetriebs die Resume-Quelle;
der offene Export ist Audit- und Exit-Format. Sobald ein neutrales Replay ohne
Informationsverlust erforderlich wird, ist DeepSeeks Eventmodell das Vorbild –
nicht ein selbst gebauter Agent Loop.

## 12. PoC- und Cache-Test-Grenzen

Der minimale PoC muss:

- ausschließlich ein Fixture-Workspace lesen,
- dieselben Tooldefinitionen für alle Modelle verwenden,
- eine Session über Prozessgrenzen fortsetzen,
- Framework-Persistenz und offenen Export erzeugen,
- OTel-Spans lokal erfassen,
- Provider/Modell nur per TOML-Profil wechseln,
- offline deterministisch testbar sein.

Er darf nicht:

- produktive Polaris-Projekte verändern,
- Curator-/Bridge-State schreiben,
- Shellzugriff anbieten,
- neue systemd-Units installieren,
- Secrets persistieren,
- LiteLLM oder einen weiteren Dienst voraussetzen.

Der Cache-Test ist ein separates, opt-in Skript. Er läuft erst, wenn ein echter
Provider verfügbar ist, und speichert pro Variante die vom Provider gemeldeten
Usage-Daten. `stable-prefix` und `mutated-prefix` müssen dieselbe fachliche
Aufgabe, Modellroute, Toolmenge und Stepzahl verwenden. Ohne echte Cache-Usage
lautet das Ergebnis „nicht messbar“, nicht „kein Cachetreffer“.

### Umgesetzter PoC-Stand (2026-09-20)

Der isolierte PoC liegt unter `experiments/agent-runtime-poc/`. Verifiziert
wurden in zwei getrennten Prozessen:

- Start, read-only `search_project`-Tool Call und native Persistenz;
- Wiederöffnung über `conversation_id`/letzten Run und `continue_run`;
- anschließender `read_project_file`-Tool Call mit fortgesetzter History;
- zwei Run-Verzeichnisse mit Events, Tool-Effect-Ledger und Snapshots;
- `polaris.session.v1` mit normalisierten Transcript-, Tool-, Boundary- und
  Summary-Events;
- lokale OTel-Spans für zwei Agent Runs, vier Modellaufrufe und zwei Tools;
- vier deterministische Offline-Tests.

Nicht ausgeführt wurden Cloud-, Ollama- und Cache-A/B-Läufe. Es waren keine
Provider-API-Keys gesetzt; der Ollama-Dienst lief nicht, und das vorhandene
Manifest verwies auf nicht vorhandene Modellblobs. Der Cache-Test ist deshalb
als expliziter, kostenpflichtiger Opt-in implementiert und lehnt Profile ohne
deklarierte providerseitige Cache-Usage ab. Es gibt bewusst kein simuliertes
Benchmarkresultat.
