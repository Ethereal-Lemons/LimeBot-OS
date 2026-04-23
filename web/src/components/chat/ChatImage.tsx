import { useState } from "react";

export function ChatImage({ src, alt }: { src: string; alt: string }) {
    const [error, setError] = useState(false);

    if (error) return null;

    return (
        <img
            src={src}
            alt={alt}
            className="max-w-full rounded-lg mb-2 max-h-64 object-cover"
            onError={() => setError(true)}
        />
    );
}
