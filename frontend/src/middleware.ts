import { defineMiddleware } from 'astro:middleware';
import { homePathForRole, type UserRole } from './lib/api/auth';
import { getSession } from './lib/api/session';

// Protected page prefixes and the roles allowed on them. This only improves
// navigation: the backend authorizes every API request on its own.
const PROTECTED_PAGES: { prefix: string; roles: UserRole[] }[] = [{ prefix: '/admin', roles: ['ADMINISTRADOR'] }];

function isUnder(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

export const onRequest = defineMiddleware(async (context, next) => {
  const path = context.url.pathname;
  const rule = PROTECTED_PAGES.find(({ prefix }) => isUnder(path, prefix));
  const isLoginPage = isUnder(path, '/login');

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
