import { defineMiddleware } from 'astro:middleware';

// Server-side page protection will be added with the authentication HUs
// (session validated against the backend). No pages are protected yet.
export const onRequest = defineMiddleware((_context, next) => next());
