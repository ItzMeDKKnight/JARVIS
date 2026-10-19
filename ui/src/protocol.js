// Protocol helpers built from the ONE shared schema file (protocol/events.schema.json),
// which the backend generates from its pydantic models. The HUD never hard-codes the
// version or the list of message types, so the two sides cannot silently drift apart.

/** @param {any} schema the parsed events.schema.json */
export function createProtocol(schema) {
  const version = schema.protocol_version;
  const eventTypes = new Set(Object.keys(schema.backend_event.discriminator.mapping));
  const commandTypes = new Set(Object.keys(schema.client_command.discriminator.mapping));

  return {
    version,
    eventTypes,
    commandTypes,

    /** Parse a backend message; returns the event or null if it isn't one we understand. */
    parseEvent(raw) {
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        return null;
      }
      if (!message || message.v !== version || !eventTypes.has(message.type)) return null;
      return message;
    },

    /** Build a command message; throws for types the backend wouldn't accept. */
    command(type, fields = {}) {
      if (!commandTypes.has(type)) throw new Error(`unknown command type: ${type}`);
      return JSON.stringify({ v: version, type, ...fields });
    },
  };
}
