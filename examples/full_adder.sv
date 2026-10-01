// A full adder built from two half adders and an OR gate.

module half_adder (
    input  logic a,
    input  logic b,
    output logic s,
    output logic c
);
  assign s = a ^ b;
  assign c = a & b;
endmodule

module or2 (
    input  logic a,
    input  logic b,
    output logic y
);
  assign y = a | b;
endmodule

module full_adder (
    input  logic a,
    input  logic b,
    input  logic cin,
    output logic s,
    output logic cout
);
  logic s1, c1, c2;

  half_adder u_ha1 (.a(a),  .b(b),   .s(s1), .c(c1));
  half_adder u_ha2 (.a(s1), .b(cin), .s(s),  .c(c2));
  or2        u_or  (.a(c1), .b(c2),  .y(cout));
endmodule
