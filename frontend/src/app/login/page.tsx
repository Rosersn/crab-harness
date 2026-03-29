"use client";

import { ChevronRightIcon, LoaderIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { startTransition, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { loadCurrentUser, login, register } from "@/core/auth/api";

type AuthMode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextUrl = useMemo(
    () => searchParams.get("next") ?? "/workspace",
    [searchParams],
  );
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCheckingSession, setIsCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;

    void loadCurrentUser()
      .then(() => {
        if (!cancelled) {
          router.replace(nextUrl);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setIsCheckingSession(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [nextUrl, router]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      if (mode === "login") {
        await login({ email, password });
      } else {
        await register({
          email,
          password,
          tenant_name: tenantName.trim() || undefined,
        });
      }

      startTransition(() => {
        router.replace(nextUrl);
      });
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Authentication failed",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingSession) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#07111f] text-white">
        <div className="flex items-center gap-3 rounded-full border border-white/15 bg-white/5 px-5 py-3 text-sm">
          <LoaderIcon className="size-4 animate-spin" />
          Restoring your session...
        </div>
      </div>
    );
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-[#07111f] text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(89,173,255,0.22),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(255,196,120,0.2),_transparent_28%),linear-gradient(180deg,_#07111f_0%,_#0d1e2b_100%)]" />
      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10">
        <div className="flex items-center justify-between">
          <Link
            className="text-sm text-white/70 transition hover:text-white"
            href="/"
          >
            DeerFlow
          </Link>
          <Link
            className="text-sm text-white/70 transition hover:text-white"
            href="/"
          >
            Back to home
          </Link>
        </div>
        <div className="grid flex-1 items-center gap-10 py-10 lg:grid-cols-[1.1fr_520px]">
          <section className="max-w-xl space-y-6">
            <div className="inline-flex rounded-full border border-sky-300/25 bg-sky-400/10 px-4 py-1 text-xs uppercase tracking-[0.24em] text-sky-100/80">
              Crab Harness 2.0
            </div>
            <div className="space-y-4">
              <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
                Sign in to the tenant-scoped workspace.
              </h1>
              <p className="max-w-lg text-base leading-7 text-white/70 sm:text-lg">
                The gateway now runs on JWT browser sessions. Log in once and
                the workspace, streaming runs, uploads, and artifact downloads
                all stay on the same authenticated session.
              </p>
            </div>
          </section>
          <Card className="border-white/10 bg-white/6 py-0 text-white shadow-2xl backdrop-blur-xl">
            <CardHeader className="border-b border-white/10 pb-6">
              <CardTitle className="text-2xl">Workspace access</CardTitle>
              <CardDescription className="text-white/60">
                Use your email account to enter the new multi-tenant gateway.
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              <Tabs
                className="gap-6"
                value={mode}
                onValueChange={(value) => {
                  setMode(value as AuthMode);
                  setError(null);
                }}
              >
                <TabsList className="grid w-full grid-cols-2 bg-white/5">
                  <TabsTrigger value="login">Login</TabsTrigger>
                  <TabsTrigger value="register">Register</TabsTrigger>
                </TabsList>
                <TabsContent value="login" className="mt-0">
                  <AuthForm
                    email={email}
                    error={error}
                    isSubmitting={isSubmitting}
                    mode="login"
                    password={password}
                    tenantName={tenantName}
                    onEmailChange={setEmail}
                    onPasswordChange={setPassword}
                    onSubmit={handleSubmit}
                    onTenantNameChange={setTenantName}
                  />
                </TabsContent>
                <TabsContent value="register" className="mt-0">
                  <AuthForm
                    email={email}
                    error={error}
                    isSubmitting={isSubmitting}
                    mode="register"
                    password={password}
                    tenantName={tenantName}
                    onEmailChange={setEmail}
                    onPasswordChange={setPassword}
                    onSubmit={handleSubmit}
                    onTenantNameChange={setTenantName}
                  />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}

function AuthForm({
  email,
  error,
  isSubmitting,
  mode,
  password,
  tenantName,
  onEmailChange,
  onPasswordChange,
  onSubmit,
  onTenantNameChange,
}: {
  email: string;
  error: string | null;
  isSubmitting: boolean;
  mode: AuthMode;
  password: string;
  tenantName: string;
  onEmailChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => Promise<void>;
  onTenantNameChange: (value: string) => void;
}) {
  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      <Field label="Email address">
        <Input
          autoComplete="email"
          className="border-white/10 bg-black/20 text-white"
          disabled={isSubmitting}
          placeholder="rose@example.com"
          type="email"
          value={email}
          onChange={(event) => onEmailChange(event.target.value)}
        />
      </Field>
      <Field label="Password">
        <Input
          autoComplete={
            mode === "login" ? "current-password" : "new-password"
          }
          className="border-white/10 bg-black/20 text-white"
          disabled={isSubmitting}
          placeholder="Enter your password"
          type="password"
          value={password}
          onChange={(event) => onPasswordChange(event.target.value)}
        />
      </Field>
      {mode === "register" && (
        <Field label="Tenant name">
          <Input
            className="border-white/10 bg-black/20 text-white"
            disabled={isSubmitting}
            placeholder="Optional, defaults to your email prefix"
            value={tenantName}
            onChange={(event) => onTenantNameChange(event.target.value)}
          />
        </Field>
      )}
      {error && (
        <div className="rounded-lg border border-rose-300/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-100">
          {error}
        </div>
      )}
      <Button
        className="w-full bg-white text-slate-950 hover:bg-slate-100"
        disabled={isSubmitting}
        type="submit"
      >
        {isSubmitting ? (
          <LoaderIcon className="size-4 animate-spin" />
        ) : (
          <ChevronRightIcon className="size-4" />
        )}
        {mode === "login" ? "Continue to workspace" : "Create account"}
      </Button>
    </form>
  );
}

function Field({
  children,
  label,
}: Readonly<{
  children: React.ReactNode;
  label: string;
}>) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-white/80">{label}</span>
      {children}
    </label>
  );
}
