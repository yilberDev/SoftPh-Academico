import { defineMiddleware } from 'astro:middleware';
import { getSession } from './lib/api/session';
import { decidePageAccess } from './lib/auth/page-access';

// Protected pages (/admin: ADMINISTRADOR, /resident: RESIDENTE) are defined in
// lib/auth/roles.ts and the decision in lib/auth/page-access.ts (HU-018). This
// only improves navigation: the backend authorizes every API request on its own.

const NO_STORE = 'no-store';

export const onRequest = defineMiddleware(async (context, next) => {
  const access = await decidePageAccess(context.url.pathname, () => getSession(context.request));

  switch (access.kind) {
    case 'public':
      return next();
    case 'unavailable':
      return new Response('El servicio no está disponible en este momento. Intenta de nuevo más tarde.', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': NO_STORE }
      });
    case 'redirect':
      return context.redirect(access.location);
    case 'forbidden': {
      const response = await context.rewrite('/forbidden');
      response.headers.set('Cache-Control', NO_STORE);
      return response;
    }
    case 'allowed': {
      context.locals.auth = access.session;
      const response = await next();
      response.headers.set('Cache-Control', NO_STORE);
      return response;
    }
  }
});
