/** Match a chat page to a supplier. Whole phrases only, never a sticky choice. */

export function foldDeskName(value) {
  return String(value || "").normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
}

function phraseIn(hay, needle) {
  const text = foldDeskName(hay);
  const name = foldDeskName(needle);
  if (!text || !name || name.length < 2) return false;
  if (text === name) return true;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp("(^|[^\\p{L}\\p{N}])" + escaped + "($|[^\\p{L}\\p{N}])", "u").test(text);
}

export function matchSuppliers(suppliers, page) {
  const shop = page && page.shop ? page.shop : "";
  const contact = page && page.contact ? page.contact : "";
  const url = page && page.url ? page.url : "";
  let path = "";
  try {
    path = new URL(url, "http://localhost").pathname;
  } catch (err) {
    path = "";
  }
  return (suppliers || []).filter((supplier) => {
    const name = supplier.name || "";
    const address = supplier.address || "";
    if (phraseIn(shop, name) || phraseIn(contact, name)) return true;
    if (address && (phraseIn(shop, address) || phraseIn(contact, address) || phraseIn(path, address))) {
      return true;
    }
    return false;
  });
}
