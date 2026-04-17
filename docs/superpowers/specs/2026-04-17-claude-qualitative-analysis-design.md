# Claude Qualitative Analysis - Design

**Datum:** 2026-04-17
**Status:** Approved

## Ziel

Integration der Anthropic Claude API zur automatischen Analyse und Bewertung qualitativer Faktoren im Hohn-Rendite Tool. Pro qualitativer Zelle soll ein Chat mit Claude gefuehrt werden koennen, um Scores zu diskutieren und anzupassen.

## Betroffene Werte

| Key | Label | Typ | Skala |
|-----|-------|-----|-------|
| judgement | Bewertungsurteil | TEXT | Freitext |
| risk_business_model | Business Model Risk | FACTOR | 0.5 - 1.5 |
| risk_regulatory | Regulatory Risk | FACTOR | 0.5 - 1.5 |
| risk_macro | Macro Risk | FACTOR | 0.5 - 1.5 |
| mgmt_participation | Management Participation | FACTOR | 0.5 - 1.5 |
| mgmt_age | Management Age | FACTOR | 0.5 - 1.5 |

Skala: 0.5 = sehr hohes Risiko/schlecht, 1.0 = neutral, 1.5 = sehr gut/geringes Risiko.
Die Werte fliessen direkt als Multiplikatoren in die Hohn-Rendite-Berechnung ein.

## User-Flow

1. User sieht leere/vorhandene qualitative Zelle im Dashboard
2. Klick auf die Zelle oeffnet einen Drawer rechts
3. Drawer zeigt: Chat-Verlauf + Score-Slider (0.5-1.5) + "Uebernehmen" Button
4. User klickt "Analyse starten" → Claude analysiert und schlaegt Score + Begruendung vor
5. User kann per Chat nachfragen, diskutieren, Kontext geben
6. User passt Score ggf. per Slider an
7. User klickt "Uebernehmen" → Score wird in company_values geschrieben
8. Chat bleibt gespeichert fuer spaetere Referenz

## Backend

### Neue Tabellen (Migration)

```
llm_conversations
  id (uuid PK)
  company_id -> companies.id (CASCADE)
  value_key (string, FK -> value_definitions.key)
  created_at (timestamp)
  updated_at (timestamp)
  UNIQUE(company_id, value_key)

llm_messages
  id (uuid PK)
  conversation_id -> llm_conversations.id (CASCADE)
  role (enum: user | assistant | system)
  content (text)
  score_suggestion (numeric, nullable) -- wenn Claude einen Score vorschlaegt
  created_at (timestamp)
```

### Neue Dependency

`anthropic>=0.40` in pyproject.toml. Neues Config-Feld: `anthropic_api_key: str = ""` in Settings.

### Neue Endpoints

`POST /api/companies/{company_id}/analyze/{value_key}`
- Erstellt/holt Conversation fuer (company, value_key)
- Baut System-Prompt mit Company-Daten + bestehenden API-Werten als Kontext
- Ruft Claude auf (claude-sonnet-4-6)
- Speichert Claude-Antwort als assistant message mit score_suggestion
- Response: `{conversation_id, message: {role, content, score_suggestion}}`

`POST /api/companies/{company_id}/chat/{value_key}`
- Body: `{message: string}`
- Haengt User-Message an bestehende Conversation
- Ruft Claude mit gesamtem Chat-Verlauf auf
- Speichert Claude-Antwort
- Response: `{message: {role, content, score_suggestion}}`

`GET /api/companies/{company_id}/chat/{value_key}/history`
- Laedt alle Messages der Conversation
- Response: `{conversation_id, messages: [{role, content, score_suggestion, created_at}]}`

### Claude Prompt

System-Prompt (mit Prompt-Caching):
```
Du bist ein erfahrener Finanzanalyst bei einem Investmentunternehmen.
Du bewertest qualitative Faktoren fuer Unternehmen auf einer Skala von 0.5 bis 1.5.

0.5 = sehr hohes Risiko / sehr schlecht
1.0 = neutral / durchschnittlich
1.5 = sehr geringes Risiko / sehr gut

Antworte immer mit:
1. SCORE: [Dezimalzahl 0.5-1.5]
2. BEGRUENDUNG: [2-3 Saetze]
3. FAKTOREN:
   - [Stichpunkt 1]
   - [Stichpunkt 2]
   - [Stichpunkt 3]

Sei praezise und nutze die bereitgestellten Finanzdaten als Grundlage.
Antworte auf Deutsch, Fachbegriffe auf Englisch.
```

User-Prompt (initial):
```
Bewerte den folgenden Aspekt fuer {company_name} ({ticker}, ISIN: {isin}):

Aspekt: {value_label_en} ({value_label_de})

Verfuegbare Finanzdaten:
{json_dump_of_existing_values}
```

Score-Extraktion: Parse "SCORE: X.XX" aus der Claude-Antwort via Regex.

### Modell

`claude-sonnet-4-6` fuer alle Aufrufe (Chat + Research). Schnell, guenstig, ausreichend fuer qualitative Bewertung.

## Frontend

### Neue Komponente: AnalysisDrawer

Slide-over Drawer (rechte Seite, 400-500px breit):
- Header: Value Label + Company Name
- Score-Anzeige: Slider (0.5-1.5, Step 0.05) + aktuelle Zahl
- Chat-Bereich: scrollbare Message-Liste (User + Assistant Bubbles)
- Input-Bereich: Textarea + "Senden" Button
- Footer: "Analyse starten" Button (falls noch keine Conversation) + "Uebernehmen" Button

### Dashboard-Integration

- Qualitative Zellen im Dashboard: statt Refresh-Icon ein Chat/Analyse-Icon
- Klick oeffnet den Drawer mit der entsprechenden (company_id, value_key) Kombination
- Wenn schon ein Score uebernommen wurde: Zelle zeigt den Score-Wert + gruenes Haekchen
- Wenn noch kein Score: Zelle zeigt "—" + Analyse-Icon

### Neue API-Funktionen

```ts
// api/llm.ts
analyzeValue(companyId, valueKey): Promise<AnalysisResponse>
sendChatMessage(companyId, valueKey, message): Promise<ChatMessageResponse>
getChatHistory(companyId, valueKey): Promise<ChatHistoryResponse>
```

## Scope-Abgrenzung

### In Scope
- Claude-Integration fuer 6 qualitative Werte
- Chat pro (Company, Value) Kombination
- Score-Slider mit Uebernahme in company_values
- Chat-Persistenz in DB
- Prompt-Caching fuer System-Prompt

### Out of Scope
- Gemini Review (spaeter)
- SSE Streaming (erste Version: synchrone Response, Streaming spaeter)
- Bulk-Analyse (alle 6 Werte auf einmal) - erst einzeln
- Auto-Analyse beim Anlegen einer Firma (spaeter)
