import { useEffect, useState } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Search, Zap, CheckCircle2, AlertCircle, Box, Layers } from 'lucide-react';
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";

interface Skill {
    id: string;
    name: string;
    description: string;
    path: string;
    active: boolean;
    type?: "limebot" | "clawhub";
}

export function SkillsPage() {
    const [skills, setSkills] = useState<Skill[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");

    useEffect(() => {
        const loadSkills = async () => {
            try {
                const res = await axios.get(`${API_BASE_URL}/api/skills`);
                setSkills(res.data.skills || []);
                setLoading(false);
            } catch (err) {
                console.error("Failed to load skills:", err);
                setLoading(false);
            }
        };
        loadSkills();
    }, []);

    const toggleSkill = async (skill: Skill, enable: boolean) => {
        // Optimistic update
        setSkills(skills.map(s => s.id === skill.id ? { ...s, active: enable } : s));

        try {
            await axios.post(`${API_BASE_URL}/api/skills/${skill.id}/toggle`, { enable });
            // The backend restarts, so we might lose connection briefly
        } catch (err) {
            console.error("Failed to toggle skill:", err);
            // Revert on error
            setSkills(skills.map(s => s.id === skill.id ? { ...s, active: !enable } : s));
        }
    };

    const filteredSkills = skills.filter(skill =>
        skill.name.toLowerCase().includes(search.toLowerCase()) ||
        skill.description.toLowerCase().includes(search.toLowerCase())
    );

    const limebotSkills = filteredSkills.filter(s => s.type !== "clawhub");
    const clawhubSkills = filteredSkills.filter(s => s.type === "clawhub");

    const SkillCard = ({ skill }: { skill: Skill }) => (
        <Card className="border-border hover:border-primary/30 transition-colors group">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                        <div className={`p-2 rounded-lg transition-colors ${
                            skill.active 
                                ? (skill.type === 'clawhub' ? 'bg-purple-500/10 text-purple-500' : 'bg-primary/10 text-primary')
                                : 'bg-muted text-muted-foreground'
                        }`}>
                            {skill.type === 'clawhub' ? <Layers className="h-5 w-5" /> : <Zap className="h-5 w-5" />}
                        </div>
                        <div>
                            <CardTitle className="text-base flex items-center gap-2">
                                {skill.name}
                                {skill.type === 'clawhub' && (
                                    <Badge variant="secondary" className="text-[10px] h-5 bg-purple-500/10 text-purple-500 hover:bg-purple-500/20 border-purple-500/20">
                                        CLAW
                                    </Badge>
                                )}
                            </CardTitle>
                        </div>
                    </div>
                    <Switch
                        checked={skill.active}
                        onCheckedChange={(checked) => toggleSkill(skill, checked)}
                    />
                </div>
            </CardHeader>
            <CardContent>
                <CardDescription className="line-clamp-3 text-sm min-h-[60px]">
                    {skill.description}
                </CardDescription>
                <div className="mt-4 pt-4 border-t border-border flex items-center justify-between text-xs text-muted-foreground font-mono">
                    <span className="truncate max-w-[150px] opacity-70">
                        {skill.type === 'clawhub' ? 'clawhub/' : 'skills/'}{skill.id}
                    </span>
                    {skill.active ? (
                        <span className={`flex items-center gap-1 ${skill.type === 'clawhub' ? 'text-purple-500' : 'text-primary'}`}>
                            <CheckCircle2 className="h-3 w-3" /> Enabled
                        </span>
                    ) : (
                        <span className="flex items-center gap-1">
                            <AlertCircle className="h-3 w-3" /> Disabled
                        </span>
                    )}
                </div>
            </CardContent>
        </Card>
    );

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-6xl mx-auto space-y-10">
                <header className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold flex items-center gap-2">
                            <Box className="h-7 w-7 text-primary" />
                            Skills Library
                        </h1>
                        <p className="text-muted-foreground mt-1">
                            Manage LimeBot's core capabilities and ClawHub extensions.
                        </p>
                    </div>
                    <div className="relative w-full md:w-72">
                        <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                            placeholder="Search skills..."
                            className="pl-9 bg-background/50"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </header>

                {loading ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {[1, 2, 3, 4, 5, 6].map(i => (
                            <div key={i} className="h-48 rounded-xl bg-muted/50 animate-pulse" />
                        ))}
                    </div>
                ) : (
                    <div className="space-y-10">
                        {/* LimeBot Core Skills Section */}
                        {limebotSkills.length > 0 && (
                            <div className="space-y-4">
                                <div className="flex items-center gap-2 pb-2 border-b border-border/50">
                                    <Zap className="h-5 w-5 text-primary" />
                                    <h2 className="text-lg font-semibold">Core Skills</h2>
                                    <Badge variant="outline" className="ml-2">{limebotSkills.length}</Badge>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                                    {limebotSkills.map(skill => (
                                        <SkillCard key={skill.id} skill={skill} />
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* ClawHub Extensions Section */}
                        {clawhubSkills.length > 0 && (
                            <div className="space-y-4">
                                <div className="flex items-center gap-2 pb-2 border-b border-border/50">
                                    <Layers className="h-5 w-5 text-purple-500" />
                                    <h2 className="text-lg font-semibold">ClawHub Extensions</h2>
                                    <Badge variant="outline" className="ml-2 border-purple-500/20 text-purple-500">{clawhubSkills.length}</Badge>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                                    {clawhubSkills.map(skill => (
                                        <SkillCard key={skill.id} skill={skill} />
                                    ))}
                                </div>
                            </div>
                        )}

                        {filteredSkills.length === 0 && (
                            <div className="col-span-full py-12 text-center text-muted-foreground">
                                <div className="inline-flex items-center justify-center p-4 rounded-full bg-muted mb-4">
                                    <Search className="h-6 w-6 opacity-50" />
                                </div>
                                <p>No skills found matching "{search}"</p>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
