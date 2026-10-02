import { defineMiddleware } from 'astro:middleware';
import { getSession } from './lib/api/session';
import { homePathForRole, isLoginPage as isLogin, protectedRule } from './lib/auth/roles';

// Protected pages (/admin: ADMINISTRADOR, /resident: RESIDENTE) are defined in
// lib/auth/roles.ts. This only improves navigation: the backend authorizes
// every API request on its own.

export const onRequest = defineMiddleware(async (context, next) => {
  const path = context.url.pathname;
  const rule = protectedRule(path);
  const isLoginPage = isLogin(path);

  if (!rule && !isLoginPage) {
    return next();
  }

  let session;
  try {
    session = await getSession(context.request);
  } catch {
    return new Response('El servicio no está disponible en este momento. Intenta de nuevo más tarde.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' }
    });
  }

  if (isLoginPage) {
    return session ? context.redirect(homePathForRole(session.user.role)) : next();
  }

  if (!session) {
    return context.redirect('/login');
  }
  if (!rule!.roles.includes(session.user.role)) {
    return context.rewrite('/forbidden');
  }

  context.locals.auth = session;
  const response = await next();
  response.headers.set('Cache-Control', 'no-store');
  return response;
});
