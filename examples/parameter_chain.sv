// A width chosen at the top and passed down two levels.

module leaf #(
    parameter int W = 2
) (
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
  assign q = ~d;
endmodule

module middle #(
    parameter int W = 2
) (
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
  logic [W-1:0] t;

  leaf #(.W(W)) u_first  (.d(d), .q(t));
  leaf #(.W(W)) u_second (.d(t), .q(q));
endmodule

module parameter_chain (
    input  logic [5:0] d,
    output logic [5:0] q
);
  middle #(.W(6)) u_middle (.d(d), .q(q));
endmodule
