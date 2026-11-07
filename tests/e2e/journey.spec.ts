import { test, expect } from "@playwright/test";
const password = process.env.DEMO_PASSWORD || "change-this-demo-password";
async function login(page: import("@playwright/test").Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", {name:/Sign in/}).click();
  await expect(page.getByRole("heading",{name:"Operational overview"})).toBeVisible();
}

test("critical workflow creates a request and explains matching", async ({page}) => {
  await login(page,"coordinator@reliefgrid.local");
  await page.getByRole("button",{name:"Requests",exact:true}).click();
  await page.getByLabel("Delivery location").selectOption({label:"Pluit Emergency Shelter"});
  await page.getByRole("combobox",{name:"Resource",exact:true}).selectOption({label:"Drinking water / liter"});
  await page.getByLabel("Quantity").fill("5");
  await page.getByLabel("Priority").selectOption("critical");
  const deadline=new Date(Date.now()+72*3600000).toISOString().slice(0,16);
  await page.getByLabel("Required by").fill(deadline);
  await page.getByLabel("Reason").fill("Synthetic urgent water requirement");
  await page.getByRole("button",{name:/Create draft/}).click();
  await expect(page.getByRole("button",{name:"Submit request"})).toBeVisible();
  await page.getByRole("button",{name:"Submit request"}).click();
  await page.getByRole("button",{name:"Verify request"}).click();
  await page.getByRole("button",{name:"Find supply matches"}).click();
  await expect(page.getByText("EXPLAINABLE MATCHES")).toBeVisible();
  await expect(page.getByRole("button",{name:"Reserve"}).first()).toBeVisible();
});

test("invalid credentials show a safe error and no workspace", async ({page}) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("unknown@synthetic.example");
  await page.getByLabel("Password").fill("invalid-long-password");
  await page.getByRole("button",{name:/Sign in/}).click();
  await expect(page.getByRole("alert").filter({hasText:"Invalid email or password"})).toContainText("Invalid email or password");
  await expect(page.getByRole("heading",{name:"Operational overview"})).not.toBeVisible();
});
