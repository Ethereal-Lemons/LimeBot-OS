import { useState } from "react";

export function ChatImage({ src, alt }: { src: string; alt: string }) {
    const [error, setError] = useState(false);

    if (error) return null;

    return (
        <img
            src={src}
            alt={alt}
            className="mb-2 max-h-[30rem] max-w-full rounded-lg border border-border/60 bg-muted/30 object-contain shadow-sm"
            onError={() => setError(true)}
        />
    );
}
