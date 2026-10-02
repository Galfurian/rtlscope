// Four full adders chained by their carries; bit selects on every pin.

module full_adder_cell (
    input  logic a,
    input  logic b,
    input  logic cin,
    output logic s,
    output logic cout
);
  assign s    = a ^ b ^ cin;
  assign cout = (a & b) | (cin & (a ^ b));
endmodule

module ripple_carry_adder (
    input  logic [3:0] a,
    input  logic [3:0] b,
    input  logic       cin,
    output logic [3:0] s,
    output logic       cout
);
  logic c1, c2, c3;

  full_adder_cell u0 (.a(a[0]), .b(b[0]), .cin(cin), .s(s[0]), .cout(c1));
  full_adder_cell u1 (.a(a[1]), .b(b[1]), .cin(c1),  .s(s[1]), .cout(c2));
  full_adder_cell u2 (.a(a[2]), .b(b[2]), .cin(c2),  .s(s[2]), .cout(c3));
  full_adder_cell u3 (.a(a[3]), .b(b[3]), .cin(c3),  .s(s[3]), .cout(cout));
endmodule
