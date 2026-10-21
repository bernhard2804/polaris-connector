/*
 * Polaris - ChatGPT-Unterhaltung roh ueber die Web-Schnittstelle holen
 * (seit 0.3.0; ab 0.4.0 nur noch Abruf, keine Interpretation).
 *
 * Dieselbe JSON-Schnittstelle, die die ChatGPT-Web-App selbst nutzt:
 *   GET /api/auth/session              -> { accessToken, expires }
 *   GET /backend-api/conversation/<id>  (Authorization: Bearer <token>)
 *     -> { conversation_id, title, current_node, gizmo_id?, update_time,
 *          mapping: { <node>: { parent, children, message } } }
 * Nicht offiziell dokumentiert. Turns bildet ausschliesslich die Bridge
 * (bridge/polaris/snapshot.py) - mit derselben Funktion wie fuer den
 * offiziellen Datenexport, der dasselbe Format hat.
 */

const PolarisChatGptApi = (() => {
  let token = null;
  let tokenExpires = 0;

  async function accessToken(fetchFn) {
    if (token && Date.now() < tokenExpires - 60_000) return token;
    const res = await fetchFn('/api/auth/session', { credentials: 'include' });
    if (!res.ok) throw new Error(`Sitzung: HTTP ${res.status}`);
    const data = await res.json();
    if (!data || typeof data.accessToken !== 'string') throw new Error('Sitzung ohne accessToken');
    token = data.accessToken;
    const exp = Date.parse(data.expires || '');
    tokenExpires = Number.isFinite(exp) ? exp : Date.now() + 10 * 60_000;
    return token;
  }

  function resetToken() {
    token = null;
    tokenExpires = 0;
  }

  async function fetchConversationRaw(conversationId, fetchFn = fetch) {
    if (!/^[0-9a-zA-Z-]+$/.test(conversationId || '')) throw new Error('ungueltige Chat-ID');
    const get = async () =>
      fetchFn(`/backend-api/conversation/${conversationId}`, {
        credentials: 'include',
        headers: { authorization: `Bearer ${await accessToken(fetchFn)}`, accept: 'application/json' },
      });
    let res = await get();
    if (res.status === 401) {
      resetToken();
      res = await get();
    }
    if (!res.ok) throw new Error(`Unterhaltung: HTTP ${res.status}`);
    const data = await res.json();
    if (!data || typeof data.mapping !== 'object' || !data.current_node) throw new Error('Antwort ohne mapping');
    const id = data.conversation_id || data.id;
    if (id && id !== conversationId) throw new Error('Antwort gehoert zu einem anderen Chat');
    return data;
  }

  return { fetchConversationRaw, resetToken };
})();
