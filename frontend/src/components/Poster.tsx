import { useState } from "react";
import { Tv } from "lucide-react";

/** Show poster that degrades to a placeholder icon when missing or broken. */
export default function Poster({ url, title, className = "h-16 w-11" }: {
  url?: string | null;
  title: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) {
    return (
      <div
        className={`flex shrink-0 items-center justify-center rounded-md bg-gray-100 dark:bg-gray-800 text-gray-300 dark:text-gray-600 ${className}`}
        aria-hidden
      >
        <Tv className="h-1/3 w-1/3" />
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={`${title} poster`}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={`shrink-0 rounded-md object-cover bg-gray-100 dark:bg-gray-800 ${className}`}
    />
  );
}
