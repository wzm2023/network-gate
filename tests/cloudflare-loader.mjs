export async function resolve(specifier, context, nextResolve) {
  if (specifier === 'cloudflare:sockets') return {
    url: 'data:text/javascript,export function connect(){throw new Error("Unexpected network in offline test")}',
    shortCircuit: true,
  };
  return nextResolve(specifier, context);
}
