import { Toaster as Sonner, type ToasterProps } from "sonner";

const Toaster = (props: ToasterProps) => {
  return <Sonner richColors closeButton {...props} />;
};

export { Toaster };
