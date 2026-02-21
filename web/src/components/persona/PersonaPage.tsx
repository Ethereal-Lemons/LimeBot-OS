import { useEffect, useState } from "react";
import axios from "axios";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../ui/card";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { Avatar, AvatarImage, AvatarFallback } from "../ui/avatar";
import { User, Sparkles, Link, MessageSquare, Save, RefreshCw, Globe, Heart, Cake, Quote, Users } from "lucide-react";

interface Relationship {
    id: string;
    name: string;
    affinity: number;
    level: string;
}

interface PersonaData {
    name: string;
    emoji: string;
    pfp_url: string;
    style: string;
    discord_style?: string;
    whatsapp_style?: string;
    web_style?: string;
    reaction_emojis?: string;
    soul_summary: string;
    catchphrases?: string;
    interests?: string;
    birthday?: string;
    mood?: string;
    enable_dynamic_personality?: boolean;
    relationships?: Relationship[];
}

export function PersonaPage() {
    const [persona, setPersona] = useState<PersonaData>({
        name: "",
        emoji: "",
        pfp_url: "",
        style: "",
        discord_style: "",
        whatsapp_style: "",
        web_style: "",
        reaction_emojis: "",
        soul_summary: "",
        catchphrases: "",
        interests: "",
        birthday: "",
        mood: "",
        enable_dynamic_personality: false,
        relationships: []
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState("");

    const fetchPersona = async () => {
        try {
            setLoading(true);
            const res = await axios.get("http://localhost:8000/api/persona");
            setPersona(res.data);
        } catch (err) {
            console.error("Failed to fetch persona:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchPersona();
    }, []);

    const handleSave = async () => {
        try {
            setSaving(true);
            const res = await axios.put("http://localhost:8000/api/persona", persona);
            if (res.data.status === "success") {
                setMessage("Persona saved successfully!");
                setTimeout(() => setMessage(""), 3000);
            } else {
                setMessage("Error: " + (res.data.error || "Unknown error"));
            }
        } catch (err) {
            setMessage("Error saving persona");
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-full">
                <RefreshCw className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* Header */}
            <div className="flex-shrink-0 flex items-center justify-between p-6 border-b border-border">
                <div>
                    <h1 className="text-2xl font-bold text-foreground">Persona</h1>
                    <p className="text-sm text-muted-foreground">
                        Configure your bot's identity and personality
                    </p>
                </div>
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    <Save className="w-4 h-4" />
                    {saving ? "Saving..." : "Save Changes"}
                </Button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-auto p-6">
                {message && (
                    <div className={`mb-4 p-3 rounded-lg text-sm ${message.includes("Error") ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"}`}>
                        {message}
                    </div>
                )}

                <div className="grid gap-6 lg:grid-cols-2">
                    {/* Preview Card */}
                    <Card className="bg-muted/30">
                        <CardHeader>
                            <CardTitle className="text-lg">Preview</CardTitle>
                            <CardDescription>How your bot appears to users</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-center gap-4 p-4 rounded-xl bg-card border border-border">
                                <Avatar className="h-16 w-16 shadow-lg">
                                    <AvatarImage src={persona.pfp_url} className="object-cover" />
                                    <AvatarFallback className="bg-primary text-primary-foreground text-xl">
                                        {persona.emoji || persona.name?.charAt(0) || "?"}
                                    </AvatarFallback>
                                </Avatar>
                                <div>
                                    <h3 className="text-xl font-bold text-foreground flex items-center gap-2">
                                        {persona.name || "Bot Name"}
                                        <span className="text-2xl">{persona.emoji}</span>
                                    </h3>
                                    <p className="text-sm text-muted-foreground mt-1 max-w-xs">
                                        {persona.style?.slice(0, 100) || "No style defined"}
                                        {persona.style?.length > 100 && "..."}
                                    </p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Soul Summary */}
                    <Card className="bg-muted/30">
                        <CardHeader>
                            <CardTitle className="text-lg flex items-center gap-2">
                                <Sparkles className="w-4 h-4 text-primary" />
                                Soul Summary
                            </CardTitle>
                            <CardDescription>Core personality from SOUL.md (read-only)</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <p className="text-sm text-muted-foreground leading-relaxed">
                                {persona.soul_summary || "No soul defined yet. Chat with your bot to develop its personality."}
                            </p>
                        </CardContent>
                    </Card>
                </div>

                {/* Edit Fields */}
                <div className="mt-6 grid gap-6">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-lg">Identity Settings</CardTitle>
                            <CardDescription>Edit your bot's public identity</CardDescription>
                        </CardHeader>
                        <CardContent className="grid gap-4">
                            <div className="grid sm:grid-cols-2 gap-4">
                                <div className="grid gap-2">
                                    <label className="text-sm font-medium flex items-center gap-2">
                                        <User className="w-4 h-4 text-muted-foreground" />
                                        Display Name
                                    </label>
                                    <Input
                                        value={persona.name}
                                        onChange={(e) => setPersona({ ...persona, name: e.target.value })}
                                        placeholder="e.g., Lisa, Sage, LimeBot"
                                    />
                                </div>

                                <div className="grid gap-2">
                                    <label className="text-sm font-medium flex items-center gap-2">
                                        <Sparkles className="w-4 h-4 text-muted-foreground" />
                                        Emoji
                                    </label>
                                    <Input
                                        value={persona.emoji}
                                        onChange={(e) => setPersona({ ...persona, emoji: e.target.value })}
                                        placeholder="e.g., 👑, 🍋, ✨"
                                    />
                                </div>
                            </div>

                            <div className="grid sm:grid-cols-2 gap-4">
                                <div className="grid gap-2">
                                    <label className="text-sm font-medium flex items-center gap-2">
                                        <Link className="w-4 h-4 text-muted-foreground" />
                                        Profile Picture URL
                                    </label>
                                    <Input
                                        value={persona.pfp_url}
                                        onChange={(e) => setPersona({ ...persona, pfp_url: e.target.value })}
                                        placeholder="https://example.com/avatar.jpg"
                                    />
                                </div>

                                <div className="grid gap-2">
                                    <label className="text-sm font-medium flex items-center gap-2">
                                        <Cake className="w-4 h-4 text-muted-foreground" />
                                        Birthday
                                    </label>
                                    <Input
                                        value={persona.birthday}
                                        onChange={(e) => setPersona({ ...persona, birthday: e.target.value })}
                                        placeholder="e.g., July 15th"
                                    />
                                </div>
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <Heart className="w-4 h-4 text-muted-foreground" />
                                    Interests
                                </label>
                                <Input
                                    value={persona.interests}
                                    onChange={(e) => setPersona({ ...persona, interests: e.target.value })}
                                    placeholder="e.g., Coding, 3D Graphics, Electronic Music"
                                />
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <Quote className="w-4 h-4 text-muted-foreground" />
                                    Catchphrases
                                </label>
                                <Input
                                    value={persona.catchphrases}
                                    onChange={(e) => setPersona({ ...persona, catchphrases: e.target.value })}
                                    placeholder="e.g., Stay sharp!; Lemons are life."
                                />
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <MessageSquare className="w-4 h-4 text-muted-foreground" />
                                    General Style / Base Personality
                                </label>
                                <Textarea
                                    value={persona.style}
                                    onChange={(e) => setPersona({ ...persona, style: e.target.value })}
                                    placeholder="The root personality of the bot..."
                                    rows={3}
                                />
                                <p className="text-xs text-muted-foreground">This is the base style used if no other platform-specific style is defined.</p>
                            </div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle className="text-lg">Platform Overrides</CardTitle>
                            <CardDescription>Tailor the bot's tone for specific chat applications</CardDescription>
                        </CardHeader>
                        <CardContent className="grid gap-4">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <Globe className="w-4 h-4 text-primary/70" />
                                    Web UI Style
                                </label>
                                <Textarea
                                    value={persona.web_style}
                                    onChange={(e) => setPersona({ ...persona, web_style: e.target.value })}
                                    placeholder={persona.style || "Fallback to General Style..."}
                                    rows={3}
                                />
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <MessageSquare className="w-4 h-4 text-[#5865F2]" />
                                    Discord Style
                                </label>
                                <Textarea
                                    value={persona.discord_style}
                                    onChange={(e) => setPersona({ ...persona, discord_style: e.target.value })}
                                    placeholder={persona.web_style || persona.style || "Fallback to Web/General Style..."}
                                    rows={3}
                                />
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <MessageSquare className="w-4 h-4 text-[#25D366]" />
                                    WhatsApp Style
                                </label>
                                <Textarea
                                    value={persona.whatsapp_style}
                                    onChange={(e) => setPersona({ ...persona, whatsapp_style: e.target.value })}
                                    placeholder={persona.web_style || persona.style || "Fallback to Web/General Style..."}
                                    rows={3}
                                />
                            </div>
                        </CardContent>
                    </Card>

                    <Card className={!persona.enable_dynamic_personality ? "opacity-50 grayscale pointer-events-none selec-none relative overflow-hidden" : ""}>
                        {!persona.enable_dynamic_personality && (
                            <div className="absolute inset-0 z-10 bg-background/50 flex items-center justify-center p-6 text-center backdrop-blur-[1px]">
                                <div className="max-w-xs">
                                    <Globe className="w-12 h-12 mx-auto mb-3 text-muted-foreground" />
                                    <h3 className="text-lg font-bold">Interactive Persona Disabled</h3>
                                    <p className="text-sm text-muted-foreground">
                                        Enable <code className="bg-muted px-1 rounded">ENABLE_DYNAMIC_PERSONALITY</code> in Config to unlock mood and relationship tracking.
                                    </p>
                                </div>
                            </div>
                        )}
                        <CardHeader>
                            <CardTitle className="text-lg flex items-center gap-2">
                                <Sparkles className="w-4 h-4 text-primary" />
                                Interactive Persona
                            </CardTitle>
                            <CardDescription>Dynamic mood and user relationship management</CardDescription>
                        </CardHeader>
                        <CardContent className="grid gap-6">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    Current Mood
                                </label>
                                <Textarea
                                    value={persona.mood}
                                    onChange={(e) => setPersona({ ...persona, mood: e.target.value })}
                                    placeholder="How are you feeling right now?"
                                    rows={2}
                                />
                                <p className="text-xs text-muted-foreground">This reflects your current internal state and affects your replies.</p>
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <Users className="w-4 h-4 text-muted-foreground" />
                                    Relationships
                                </label>
                                <div className="border rounded-lg overflow-hidden">
                                    <table className="w-full text-sm">
                                        <thead className="bg-muted/50 border-b">
                                            <tr>
                                                <th className="text-left p-2 font-medium">User</th>
                                                <th className="text-left p-2 font-medium">Level</th>
                                                <th className="text-center p-2 font-medium text-primary">Affinity</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {persona.relationships && persona.relationships.length > 0 ? (
                                                persona.relationships.map((rel) => (
                                                    <tr key={rel.id} className="border-b last:border-0 hover:bg-muted/20 transition-colors">
                                                        <td className="p-2 font-medium">{rel.name}</td>
                                                        <td className="p-2 text-muted-foreground">{rel.level}</td>
                                                        <td className="p-2 text-center text-primary font-bold">{rel.affinity}</td>
                                                    </tr>
                                                ))
                                            ) : (
                                                <tr>
                                                    <td colSpan={3} className="p-4 text-center text-muted-foreground">No relationships recorded yet.</td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium flex items-center gap-2">
                                    <Sparkles className="w-4 h-4 text-amber-500" />
                                    Autonomous Reactions
                                </label>
                                <Input
                                    value={persona.reaction_emojis}
                                    onChange={(e) => setPersona({ ...persona, reaction_emojis: e.target.value })}
                                    placeholder="happy:😊,😂; sad:😢,😭; ..."
                                />
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
