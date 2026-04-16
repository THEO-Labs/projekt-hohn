import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/LoginPage";

describe("LoginPage", () => {
  it("ruft onLogin mit eingegebenen Credentials", async () => {
    const onLogin = vi.fn().mockResolvedValue(undefined);
    render(<LoginPage onLogin={onLogin} />);

    await userEvent.type(screen.getByLabelText(/E-Mail/i), "t@example.com");
    await userEvent.type(screen.getByLabelText(/Passwort/i), "pw1234");
    await userEvent.click(screen.getByRole("button", { name: /Anmelden/i }));

    expect(onLogin).toHaveBeenCalledWith("t@example.com", "pw1234");
  });

  it("zeigt Fehler an wenn Login fehlschlaegt", async () => {
    const onLogin = vi.fn().mockRejectedValue(new Error("nope"));
    render(<LoginPage onLogin={onLogin} />);

    await userEvent.type(screen.getByLabelText(/E-Mail/i), "t@example.com");
    await userEvent.type(screen.getByLabelText(/Passwort/i), "pw1234");
    await userEvent.click(screen.getByRole("button", { name: /Anmelden/i }));

    expect(await screen.findByText(/Anmeldung fehlgeschlagen/i)).toBeInTheDocument();
  });
});
