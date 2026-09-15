import type { CSSProperties } from "react";
/** Recursos estáticos servidos desde `public/`.
 * En el build la app vive bajo /static/app/, así que resolvemos con BASE_URL
 * para que las rutas funcionen igual en desarrollo y en producción. */
export const asset = (name: string) => `${import.meta.env.BASE_URL}img/${name}`;
/** Siluetas de monos usadas como ilustración, teñidas por CSS con el acento. */
export const monkeyMask = (name: string) =>
  ({ "--monkey-src": `url("${asset(name)}")` }) as CSSProperties;
